# Avi Performance testing utilities

## Introduction

These set of scripts are useful for performance and scale testing SSL/TLS Transactions/sec for the Avi Vantage product. Initial testing was performed on GCP.

Refer to this blog (TBI) for an overview and details.

## Requirements

- GCP account with privileges to create VMs, program routes, etc.
- Available quota for cores to perform the test 

## Getting Started in GCP

- To reach a million TPS, you need atleast 40 instances of type n1-highcpu-32 and 320 instances of type n1-highcpu-16 for use. Create or use a /23 subnet with sufficient IP addresses
- Create a custom centos7 image with packages docker, psmisc and httpd-tools installed using these [instructions](https://cloud.google.com/compute/docs/images/create-delete-deprecate-private-images) from GCP
- Create a n1-standard-4 instance, download and start a Avi Controller instance following the instructions here (TBI)
- Create another g1-small instance with scopes ‘compute-rw’ for use as a bootstrap instance to run these test scripts. git clone or copy these scripts to this instance

## Configure config.yaml

Modify config.yaml appropriate to your environment.

### clouddata

**prefix**: Prefix to be used for instances  
**ssh_username**: username to be used for login to instances  
**ssh_public_key**: ssh public key in key pair. Public key to be added as an “authorized_key” for all instances  
**ssh_private_key**: corresponding ssh private key  

### avicontroller

**api_endpoint**: IP Address of Avi Controller  
**username**: admin or other username  
**password**: password for authentication  
**network**: Network name for IP Address pool to be created  
**ipam**: IPAM Provider profile name  
**ipam_subnet**: Subnet from which a VIP will be allocated. This subnet should not overlay with any existing subnet in GCP. It can be any subnet - RFC1918 (private) or public. The VIP will just be accessed within GCP  
**ipam_start, ipam_start**: Just a single IP address in this subnet is sufficient for GCP  
**datascript**: name of the datascript to be created  
**virtualservice**: name of the virtualservice to be created  
vip: Use the single IP Address in the IP Address pool  
**placement_subnet**: Use the subnet the instances are connected to  

### pool

**project**: GCP project the experiment is running in  
**zone**: GCP zone the experiment is running in  
**subnet**: Subnet the instance are connected to. Its part of the ‘*subnetwork*’ field for an instance. ‘*gcloud compute describe instance instancename*’ shows details of an instance including its network and subnetwork. Just provide the portion of the ‘*subnetwork*’ field from ‘*projects/…*’  
**tags**: tags that allow port 80/443 access for instance  
**external_access**: Set to true if next field yum_install is true  
**yum_install**: Set to true to install docker on instance  

### avise

**image_project**: GCP project where custom image is created - same as the project where the test is running  
**external_access**: Set to true, since Avi ServiceEngines require GCP API access. If GCP API access is available via some other mechanism, this can be set to false  

### client

**preemptible**: Set to yes if you wish to create pre-emptible instances as test clients. Note that pre-emptible instances cost less, but can be terminated at any time  
**client_threads**: Set to same number as number of cores for instance. If instance type is n1-highcpu-16, set to 16  
**external_access**: set to false. External IP isn’t needed  
**yum_install**: Custom image has ‘*ab*’ pre-installed  
