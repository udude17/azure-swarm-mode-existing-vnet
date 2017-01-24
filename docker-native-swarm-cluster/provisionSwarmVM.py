#!/usr/bin/python

# Maschine muss mit azure python modulen vorbereitet sein: pip install --pre azure
# pip muss installiert werden

# provisionSwarmVM.py --role manager --storageAccount swrm2storage --storageAccountKey 4H8QmERqWZcK/R7llkJR72YxOAhDhaABj19JMuM6kf9QCoySlD8zsrIXwutrh74DCks53fFRGuwE7zDwSskAAw== --dockerInterfaceName enp0s8 --joinIp 192.168.33.111

# Open Issues:
# - If once accidentially two nodes decide to initialize the cluster, a random number could be introduced to wait before initializing to tear apart the start if provisioning
#    - Additionally we could check at the end of the initialization if another instance finished initialization earlier. If so - clear local swarm and join the one that was created earlier.

import sys, getopt, time, socket, subprocess

from azure.storage.table import TableService, Entity

print('Number of arguments:', len(sys.argv), 'arguments.')
print('Argument List:', str(sys.argv))

# Constants
global MANAGER, WORKER, TOKENS_TABLE, INIT_TABLE, STATE_INITIALIZING, STATE_INITIALIZED
MANAGER = "manager"
WORKER = "worker"
TOKENS_TABLE = "SwarmTokens"
INIT_TABLE = "SwarmInitialization"
STATE_INITIALIZING = "INITIALIZING"
STATE_INITIALIZED = "INITIALIZED"

# global variables
global table_service

table_service = None


def main(argv):
    "Main Method"
    role = "undefined"
    storage_account_name = "undefined"
    storage_account_key = "undefined"
    join_ip = "undefined"
    docker_interface_name = "undefined"
    try:
        opts, args = getopt.getopt(argv, "r:s:k:i:n:", ["role=", "storageAccount=", "storageAccountKey=", "joinIp=",
                                                        "dockerInterfaceName="])
    except getopt.GetoptError:
        print('provisionSwarmVM.py --role <role>')

    for opt, arg in opts:
        if opt in ("-r", "--role"):
            if arg.lower() in ("manager", "worker"):
                role = arg.lower()
            else:
                printUsage()
        elif opt in ("-s", "--storageAccount"):
            storage_account_name = arg
        elif opt in ("-k", "--storageAccountKey"):
            storage_account_key = arg
        elif opt in ("-i", "--joinIp"):
            join_ip = arg
        elif opt in ("-n", "--dockerInterfaceName"):
            docker_interface_name = arg

    initTableStorage(storage_account_name, storage_account_key)

    if role == MANAGER and isSwarmNotInitialized():
        print("No Swarm initialized or currently initializing. I'm the first one and now initializing the swarm...")
        initializeSwarm(docker_interface_name)
    elif role == MANAGER and isSwarmInitialized():
        print("Swarm existing")
        joinAsManager(join_ip, docker_interface_name)
    elif role == MANAGER and isSwarmCurrentlyInitializing():
        waitForInitialization()
        joinAsManager(join_ip, docker_interface_name)
    elif role == WORKER:
        joinAsWorker(join_ip, docker_interface_name)
    else:
        print("Nothing...")
        # raise ValueError('No Idea what to do at current State.')


def initializeSwarm(docker_interface_name):
    "Initializes the swarm and stores the information to the tablestorage"
    # 1. tablestorage schreiben, dass ich gerade initialisiere!
    # table_service = TableService() #uncomment for code completion

    # At first - write to table storage that I'm currently initializing!
    currentTimeMs = int(round(time.time() * 1000))
    hostname = socket.gethostname()

    entity = {'PartitionKey': 'init', 'RowKey': str(currentTimeMs), 'Machine': hostname, 'State': STATE_INITIALIZING}
    table_service.insert_entity(INIT_TABLE, entity)

    # Initialize Swarm
    executeShell("docker swarm init --advertise-addr " + docker_interface_name)

    storeSwarmTokens()

    # Start Marker Service
    start_marker_docker_container()

    # sleep 60 seconds for the probe to recognize the working element in the pool
    time.sleep(60)

    entity = {'PartitionKey': 'init', 'RowKey': str(currentTimeMs), 'Machine': hostname, 'State': STATE_INITIALIZED}
    table_service.update_entity(INIT_TABLE, entity)

    set_manager_drain_mode()


def set_manager_drain_mode():
    "set manager node availability to 'drain' so that the manager node will not be scheduled for containers. Only workers shall start containers"
    executeShell("docker node update --availability drain " + socket.gethostname())


def start_marker_docker_container():
    "Starts a docker container as marker for the load balancer probes"
    executeShell("docker run -itd -p 23980:80 --name SwarmMarkerContainer --restart always nginx:latest",
                 timeout=180,
                 ignore_shell_errors=True)


def executeShell(command, ignore_shell_errors=False, timeout=20):
    print("SHELL: " + command)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    process.wait(timeout)
    if process.returncode != 0 and not ignore_shell_errors:
        raise ValueError("Shell threw error. Stdout: ", process.stdout.read(), " StdErr: ", process.stderr.read())

    return str(process.stdout.read())


def storeSwarmTokens():
    "issues docker swarm join-token... an stores the manager and worker token to the tablestorage"

    # manager token
    output = executeShell("docker swarm join-token manager")
    manager_token = find_token_from_output(output)

    # worker token
    output = executeShell("docker swarm join-token worker")
    worker_token = find_token_from_output(output)

    # write these tokens to the tablestorage
    entity = {'PartitionKey': 'tokens', 'RowKey': 'tokens', 'ManagerToken': manager_token, 'WorkerToken': worker_token}
    table_service.insert_or_replace_entity(TOKENS_TABLE, entity)


def retrieveSwarmTokens():
    # table_service = TableService()  # uncomment for code completion
    entity_list = table_service.query_entities(
        table_name=TOKENS_TABLE
    )

    token_data = None
    # Usually we should have exactly one entry, but iterate until the last
    for entity in entity_list:
        token_data = entity

    if token_data == None:
        raise ValueError("No token information found in tablestorage!")

    return {'manager_token': token_data.ManagerToken, "worker_token": token_data.WorkerToken}


def find_token_from_output(dockerswarm_jointokens_output):
    "Extracts the token from output of docker swarm join-token..."
    tokens = str(dockerswarm_jointokens_output).split()

    token = None
    token_found = False
    for element in tokens:
        if token_found:
            token = element
            token_found = False
            break
        if element == '--token':
            token_found = True

    if token == None:
        raise ValueError('No Token found in docker swarm join-token output: ', dockerswarm_jointokens_output)

    return token


def joinAsManager(join_ip, docker_interface_name):
    "Joins the existing swarm as a manager node"
    print('Joining Swarm as MANAGER...')
    tokens = retrieveSwarmTokens()
    command = "docker swarm join --token %(token)s --advertise-addr %(docker_interface_name)s %(join_ip)s:2377" \
              % {'token': tokens['manager_token'], 'join_ip': join_ip, "docker_interface_name": docker_interface_name}
    executeShell(command)
    start_marker_docker_container()
    set_manager_drain_mode()


def waitForInitialization():
    "Waits until another node finished initialization"
    print("Swarm currently initializing. Waiting for completion...")

    isInitializing = True

    # TODO: Abort after long timeout

    while isInitializing:
        print("wait...")
        time.sleep(10)

        entity_list = table_service.query_entities(
            table_name=INIT_TABLE,
            filter="State eq 'INITIALIZING'",
        )
        found_entities = 0
        for entity in entity_list:
            found_entities += 1

        isInitializing = found_entities > 0


def joinAsWorker(join_ip, docker_interface_name):
    "Joins the swarm as worker, based on the values stored in the tablestorage"
    print('Joining Swarm as WORKER...')
    tokens = retrieveSwarmTokens()
    command = "docker swarm join --token %(token)s --advertise-addr %(docker_interface_name)s %(join_ip)s:2377" \
              % {'token': tokens['worker_token'], 'join_ip': join_ip, 'docker_interface_name': docker_interface_name}
    executeShell(command)
    start_marker_docker_container()


def isSwarmInitialized():
    "returns if the swarm is already initialized (on base of the information in the tablestorage). If true, the swarm can be joined directly via the manager LoadBalancer"
    # table_service = TableService() #uncomment for code completion
    entity_list = table_service.query_entities(
        table_name=INIT_TABLE,
        filter="State eq 'INITIALIZED'",
    )
    found_entities = 0
    for entity in entity_list:
        found_entities += 1

    print("Found INITIALIZED Entities: ", found_entities)
    return found_entities > 0


def isSwarmCurrentlyInitializing():
    "returns if - according to the tablestorage - the swarm is currently initialized by another node"
    # table_service = TableService() #uncomment for code completion

    entity_list = table_service.query_entities(
        table_name=INIT_TABLE,
        filter="State eq 'INITIALIZING'",
    )
    found_entities = 0
    for entity in entity_list:
        found_entities += 1

    print("Found INITIALIZING Entities: ", found_entities)
    return found_entities > 0


def isSwarmNotInitialized():
    "returns if - according to the tablestorage - the swarm is not yet initialized and no noe else is currently initializing it."
    # table_service = TableService() #uncomment for code completion

    entity_list = table_service.query_entities(
        table_name=INIT_TABLE,
        filter="State eq 'INITIALIZED' or State eq 'INITIALIZING'",
    )
    found_entities = 0
    for entity in entity_list:
        found_entities += 1

    print("Found INITIALIZED or INITIALIZING Entities: ", found_entities)
    return found_entities == 0


def initTableStorage(account_name, account_key):
    "Initializes Azure Table Storage and clients"
    global table_service
    table_service = TableService(account_name=account_name, account_key=account_key)

    table_service.create_table(INIT_TABLE)
    table_service.create_table(TOKENS_TABLE)


def printUsage():
    "prints Usage and exits"
    print(
        'Usage: provisionSwarmVM.py --role [manager|worker] --storageAccount <StorageAccountName> --storageAccountKey <StorageAccountKey> --joinIp <joinIp>')
    sys.exit(1)


# Run main Method
if __name__ == "__main__":
    main(sys.argv[1:])
