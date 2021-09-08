#!/usr/bin/env python3
import os
import tempfile
import time
import googleapiclient.discovery
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
from Crypto.PublicKey import RSA
from pprint import pprint

# Use the application default credentials
cred = credentials.ApplicationDefault()
firebase_admin.initialize_app(cred, {
  'projectId': 'virtual-lab-1',
})

# get db hande
db = firestore.client()

# Setup details
project = "virtual-lab-1"
region = "us-central1"
zone = "us-central1-a"

# get the compute
compute = googleapiclient.discovery.build('compute', 'v1')
print('Creating instance.')

# Get the custom image
image_response = compute.images().get(
    project='virtual-lab-1', image='introk8s-image').execute()
source_disk_image = image_response['selfLink']

# setup the VPC network configuration
# This is for the dataplane VPC
# Custom mode. Do not automatically create subnets
network_body = {
    'name': 'dataplane',
    'autoCreateSubnetworks': False,
}

# Create a subnetwork in one region only
# Need to figure out how to ensure ipCidrRange does not clash
# with the default subnet
subnetwork_body = {
    'name': 'dataplane',
    'network': 'global/networks/dataplane',
    'ipCidrRange': '10.129.0.0/20',
}

# Setup the instance configuration
instance_config = {
    'name': None,
    'machineType': None,
    'canIpForward': True,

    # Specify the boot disk and the image to use as a source.
    'disks': [
        {
            'boot': True,
            'autoDelete': True,
            'initializeParams': {
                'sourceImage': source_disk_image,
                'diskSizeGb': 100,
            }
        }
    ],

    # Setup the ssh-keys in the instance metadata
    'metadata': {
        'fingerprint': None,
        'items': [
            {
                # ssh-keys
                'key': 'ssh-keys',
                'value': None
            }
        ]
    },

    # Specify a network interface with NAT to access the public
    # internet.
    'networkInterfaces': [{
#       'network': 'global/networks/default',
        'subnetwork': 'regions/'+region+'/subnetworks/default',
        'accessConfigs': [
            {'type': 'ONE_TO_ONE_NAT', 'name': 'External NAT'}
        ]
                          },
                          {
#       'network': 'global/networks/dataplane',
        'subnetwork': 'regions/'+region+'/subnetworks/dataplane',
        'accessConfigs': [
            {'type': 'ONE_TO_ONE_NAT', 'name': 'Data Plane'}
        ]
                          },
    ],

    # Allow the instance to access cloud storage and logging.
    'serviceAccounts': [{
        'email': 'default',
        'scopes': [
            'https://www.googleapis.com/auth/cloud-platform',
        ]
    }],

    'labels': {
        'user': None,
    },
}


#
# Wait for operation to complete
#
def wait_for_operation(compute, project, zone, operation):
    print('Waiting for operation to finish...')
    while True:
        result = compute.zoneOperations().get(
            project=project,
            zone=zone,
            operation=operation).execute()

        if result['status'] == 'DONE':
            print("done.")
            if 'error' in result:
                raise Exception(result['error'])
            return result

        time.sleep(1)

#
# VPC network create is a global operation
#
def wait_for_global_operation(compute, project, operation):
    print('Waiting for global operation to finish...')
    while True:
        result = compute.globalOperations().get(
            project=project,
            operation=operation).execute()

        if result['status'] == 'DONE':
            print("done.")
            if 'error' in result:
                raise Exception(result['error'])
            return result

        time.sleep(1)


#
# VPC subnetwork create is a regional operation
#
def wait_for_regional_operation(compute, project, region, operation):
    print('Waiting for region operation to finish...')
    while True:
        result = compute.regionOperations().get(
            project=project,
            region=region,
            operation=operation).execute()

        if result['status'] == 'DONE':
            print("done.")
            if 'error' in result:
                raise Exception(result['error'])
            return result

        time.sleep(1)


#
# Create multiple VM instances
#
def create_instance(email):

    # check if the documet already exists
    doc_ref = db.collection(u'dataplane').document(email)
    doc = doc_ref.get()

    if doc.exists:
        print(f'Document exists: Nothing to be done')
        return
    else:
        print(u'No such document!')

    # insert a document
    doc_ref.set({
        u'email': email,
    }, merge=True)

    # Derive a vm instance name from the emai id.
    # Remove the "@" symbols in the email since that is not accepted in vm-instance name
    vm_name_prefix = email.replace("@", "-")
    vm_name_prefix = vm_name_prefix.replace(".", "-")

    # Generate the public-private pair
    key = RSA.generate(2048)

    # get the public key to be inserted into the instance metadata
    # write out the private key to a file. This will sent as an attachment
    # to the user
    public_key = key.publickey().exportKey('OpenSSH').decode('utf-8')
    private_key = key.exportKey('PEM').decode('utf-8')

    # Insert the keys into the db
    doc_ref.set({
      u'ssh_public_key': public_key,
    }, merge=True)
    doc_ref.set({
      u'ssh_private_key': private_key,
    }, merge=True)

    # create the dataplane VPC network first
    request = compute.networks().insert(project=project, body=network_body)
    operation = request.execute()

    # Check the response
    pprint(operation)

    # wait for the operation to complete
    result = wait_for_global_operation(compute, project, operation['name'])

    # create the dataplane subnet
    request = compute.subnetworks().insert(project=project, region=region, body=subnetwork_body)
    operation = request.execute()

    # Check the response
    pprint(operation)

    # wait for the operation to complete
    result = wait_for_regional_operation(compute, project, region, operation['name'])

    # Create the master VM
    vm_m_name = "%s-k8s-master" % vm_name_prefix
    instance_config['name'] = vm_m_name
    instance_config['machineType'] = "zones/%s/machineTypes/n1-standard-2" % zone
    instance_config['metadata']['items'][0]['value'] = "kuberack:%s kuberack" % public_key
    instance_config['labels']['user'] = vm_name_prefix
    print(vm_m_name)

    operation =  compute.instances().insert(
                    project=project,
                    zone=zone,
                    body=instance_config).execute()

    result = wait_for_operation(compute, project, zone, operation['name'])
    doc_ref.set({
      u'master': operation['name'],
    }, merge=True)

    # Create the instance-1
    vm_1_name = "%s-instance-1" % vm_name_prefix
    instance_config['name'] = vm_1_name
    instance_config['machineType'] = "zones/%s/machineTypes/n1-standard-1" % zone
    instance_config['metadata']['items'][0]['value'] = "kuberack:%s kuberack" % public_key
    instance_config['labels']['user'] = vm_name_prefix
    print(vm_1_name)

    operation =  compute.instances().insert(
                    project=project,
                    zone=zone,
                    body=instance_config).execute()

    result = wait_for_operation(compute, project, zone, operation['name'])
    doc_ref.set({
      u'instance-1': operation['name'],
    }, merge=True)

    # Create the instance-2
    vm_2_name = "%s-instance-2" % vm_name_prefix
    instance_config['name'] = vm_2_name
    instance_config['name'] = "%s-instance-2" % vm_name_prefix
    instance_config['machineType'] = "zones/%s/machineTypes/n1-standard-1" % zone
    instance_config['metadata']['items'][0]['value'] = "kuberack:%s kuberack" % public_key
    instance_config['labels']['user'] = vm_name_prefix
    print(vm_2_name)

    operation =  compute.instances().insert(
                    project=project,
                    zone=zone,
                    body=instance_config).execute()

    result = wait_for_operation(compute, project, zone, operation['name'])
    doc_ref.set({
      u'instance-2': operation['name'],
    }, merge=True)

    # get the external IPs associated with the instances
    result = compute.instances().get(project=project, zone=zone, instance=vm_m_name).execute()
    m_ip = result['networkInterfaces'][0]['accessConfigs'][0]['natIP']

    result = compute.instances().get(project=project, zone=zone, instance=vm_1_name).execute()
    i1_ip = result['networkInterfaces'][0]['accessConfigs'][0]['natIP']

    result = compute.instances().get(project=project, zone=zone, instance=vm_2_name).execute()
    i2_ip = result['networkInterfaces'][0]['accessConfigs'][0]['natIP']

    print('getting the IPs now')
    print(m_ip)
    print(i1_ip)
    print(i2_ip)
    print(vm_m_name)
    print(vm_1_name)
    print(vm_2_name)

    # Generate the public-private pair. This is for signing the JWT
    key = RSA.generate(2048)
    jwt_public_key = key.publickey().exportKey('PEM').decode('utf-8')
    jwt_private_key = key.exportKey('PEM').decode('utf-8')

    # Insert the key into the db
    doc_ref.set({
      u'jwt_public_key': jwt_public_key,
    }, merge=True)
    doc_ref.set({
      u'jwt_private_key': jwt_private_key,
    }, merge=True)

    # write the private key to a file. This will be used to sign the JWT
    # private_key = key.exportKey()
    # file_out = open("/tmp/"+email+"-jwt-private.pem", "wb")
    # file_out.write(private_key)
    # file_out.close()

    # sign the JWT
    # jwt = create_jwt(email, jwt_private_key, 'RS256')

    # actually send the mail. Will have the following items
    # ips, ssh private key, JWT, and the kr_cloud.py
    # mail_resp = send_mail(email, m_ip, i1_ip, i2_ip, jwt)

    return result

#
# Create multiple VM instances
#
def create_instances(request):

    # get this from request header
    project = 'ipsec'

    result = create_instance(project)

    return result

def create_instances_onprem():

    # get this from request header
    project = 'ipsec'

    result = create_instance(project)

    return result

if __name__ == '__main__':
    
    result = create_instances_onprem()
