# azure-resources
This repository contains 1. all my favourite azure commands and 2. all my favourite azure templates.


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
