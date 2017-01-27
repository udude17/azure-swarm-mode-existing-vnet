# azure-resources
This repository is a based on this one: https://github.com/peez80/azure-resources/

# Commands

## Login
    azure login

## Select Subscription

## Create Resource Group
    azure group create PhlLearnRG "westeurope"

## deploy template to resource group
    azure group deployment create -f azuredeploy.json -g PhlLearnRG -n MyDeployment

## purge resource group
    azure group deployment create -f purgeresourcegroup.json -g PhlLearnRG -n PurgeDepl --mode Complete
