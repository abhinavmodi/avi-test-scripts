#!/usr/bin/python

import logging, sys, argparse, json, yaml, os, traceback
from gcp import gcp

def deleteclient(cloud_obj, log):
    prefix = '%sclient-' % cloud_obj.cloud['clouddata']['prefix']
    cloud_obj.delete_instances(cloud_obj.cloud['clouddata']['client'], prefix)

def deletepool(cloud_obj, log):
    prefix = '%spool-' % cloud_obj.cloud['clouddata']['prefix']
    cloud_obj.delete_instances(cloud_obj.cloud['clouddata']['pool'], prefix)

def starttest(cloud_obj, log):
    prefix = '%sclient-' % cloud_obj.cloud['clouddata']['prefix']
    num_instances = cloud_obj.cloud['clouddata']['client'].get('instances', 1)
    cloud_obj.start_test(cloud_obj.cloud['clouddata']['client'], 
                         cloud_obj.cloud['clouddata']['avicontroller']['vip'], 
                         prefix, num_instances)

def stoptest(cloud_obj, log):
    prefix = '%sclient-' % cloud_obj.cloud['clouddata']['prefix']
    cloud_obj.stop_test(cloud_obj.cloud['clouddata']['client'], prefix)

def createclient(cloud_obj, log):
    prefix = '%sclient-' % cloud_obj.cloud['clouddata']['prefix']
    num_instances = cloud_obj.cloud['clouddata']['client'].get('instances', 1)
    ii = cloud_obj.create_client(cloud_obj.cloud['clouddata']['client'], 
            prefix, num_instances, cloud_obj.cloud['clouddata']['ssh_username'],
            cloud_obj.cloud['clouddata']['ssh_public_key'])
    log.info('Num client instances running %d prefix %s' % (len(ii), prefix))

def createpool(cloud_obj, log):
    prefix = '%spool-' % cloud_obj.cloud['clouddata']['prefix']
    num_instances = cloud_obj.cloud['clouddata']['pool'].get('instances', 1)
    cloud_obj.create_pool(cloud_obj.cloud['clouddata']['pool'], 
            prefix, num_instances, cloud_obj.cloud['clouddata']['ssh_username'],
            cloud_obj.cloud['clouddata']['ssh_public_key'])

def deletese(cloud_obj, log):
    prefix = '%savise-' % cloud_obj.cloud['clouddata']['prefix']
    cloud_obj.delete_ses(cloud_obj.cloud['clouddata']['avise'],
            cloud_obj.cloud['clouddata']['avicontroller'], prefix)

def createse(cloud_obj, log):
    prefix = '%savise-' % cloud_obj.cloud['clouddata']['prefix']
    num_instances = cloud_obj.cloud['clouddata']['avise'].get('instances', 1)
    n = cloud_obj.create_ses(cloud_obj.cloud['clouddata']['avise'],
            cloud_obj.cloud['clouddata']['avicontroller'],
            prefix, num_instances, cloud_obj.cloud['clouddata']['ssh_username'],
            cloud_obj.cloud['clouddata']['ssh_public_key'],
            cloud_obj.cloud['clouddata']['ssh_private_key'])
    log.info('Num SE instances running %d prefix %s' % (n, prefix))

def createvs(cloud_obj, log):
    pool_prefix = '%spool-' % cloud_obj.cloud['clouddata']['prefix']
    n_pool_instances = cloud_obj.cloud['clouddata']['pool'].get('instances', 1)
    cloud_obj.create_vs(cloud_obj.cloud['clouddata']['avicontroller'],
            cloud_obj.cloud['clouddata']['pool'], pool_prefix, n_pool_instances)

def createcloud(cloud_obj, log):
    cloud_obj.create_cloud(cloud_obj.cloud['clouddata']['avicontroller'],
            cloud_obj.cloud['clouddata']['avise'],
            cloud_obj.cloud['clouddata']['ssh_username'],
            cloud_obj.cloud['clouddata']['ssh_public_key'],
            cloud_obj.cloud['clouddata']['ssh_private_key'])

def deletevs(cloud_obj, log):
    cloud_obj.delete_vs(cloud_obj.cloud['clouddata']['avicontroller'],
            cloud_obj.cloud['clouddata']['pool'])

def deletecloud(cloud_obj, log):
    cloud_obj.delete_cloud(cloud_obj.cloud['clouddata']['avicontroller'],
            cloud_obj.cloud['clouddata']['ssh_username'])

def createall(cloud_obj, log):
    createcloud(cloud_obj, log)
    createpool(cloud_obj, log)
    createse(cloud_obj, log)
    createvs(cloud_obj, log)
    createclient(cloud_obj, log)

def deleteall(cloud_obj, log):
    deleteclient(cloud_obj, log)
    deletevs(cloud_obj, log)
    deletepool(cloud_obj, log)
    deletese(cloud_obj, log)
    deletecloud(cloud_obj, log)

if __name__ == "__main__":
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    parser = argparse.ArgumentParser(description='Avi Performance Gen')
    action_choices = ['createall', 'createcloud', 'createvs', 'createclient', 'createse', 'createpool', 'deletevs', 'starttest', 'stoptest', 'deleteclient', 'deletese', 'deletepool', 'deletecloud', 'deleteall']
    parser.add_argument('--action', '-a', action='store', required=True,
                        help='action - one of createall|createcloud|createclient|createse|starttest|stoptest|deleteall|deletecloud|deleteclient|deletese',
                        choices=action_choices)
    parser.add_argument('--file', '-f', action='store', required=True,
                        help='config file in YAML or JSON format')
    args = parser.parse_args()

    filename, file_extension = os.path.splitext(args.file)
    if file_extension == '.yaml':
        with open(args.file) as f:
            try:
                cloud_data = yaml.load(f)
            except Exception:
                logger.error('Exc %s opening file %s' % (traceback.format_exc(),
                    args.file))
                raise
    elif file_extension == '.json':
        with open(args.file) as f:
            try:
                cloud_data = json.load(f)
            except Exception:
                logger.error('Exc %s opening file %s' % (traceback.format_exc(),
                    args.file))
                raise

    if cloud_data['clouddata']['kind'] == 'gcp':
        cloud_obj = gcp(cloud_data, logger)

    if args.action == 'createclient':
        createclient(cloud_obj, logger)
    elif args.action == 'createse':
        createse(cloud_obj, logger)
    elif args.action == 'createvs':
        createvs(cloud_obj, logger)
    elif args.action == 'createpool':
        createpool(cloud_obj, logger)
    elif args.action == 'createcloud':
        createcloud(cloud_obj, logger)
    elif args.action == 'createall':
        createall(cloud_obj, logger)
    elif args.action == 'starttest':
        starttest(cloud_obj, logger)
    elif args.action == 'stoptest':
        stoptest(cloud_obj, logger)
    elif args.action == 'deletese':
        deletese(cloud_obj, logger)
    elif args.action == 'deleteclient':
        deleteclient(cloud_obj, logger)
    elif args.action == 'deletevs':
        deletevs(cloud_obj, logger)
    elif args.action == 'deletepool':
        deletepool(cloud_obj, logger)
    elif args.action == 'deletecloud':
        deletecloud(cloud_obj, logger)
    elif args.action == 'deleteall':
        deleteall(cloud_obj, logger)
    else:
        logger.error('Unsupported option %s' % args.action)
