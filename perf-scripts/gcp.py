import time, traceback, json, httplib2
from cloud import Cloud
from oauth2client.client import GoogleCredentials
from googleapiclient import discovery
from avi.sdk.avi_api import ApiSession

from fabric.api import env as fabric_env
from fabric.api import task, sudo, execute, run

from fabric.context_managers import settings
from fabric.state import output as fabric_output
import os

@task
def install_ab_task(image_family='centos'):
    if 'centos' in image_family:
        sudo('yum install -y httpd-tools psmisc')

@task
def install_docker_task(image_family='centos'):
    if 'centos' in image_family:
        res = sudo('yum install -y docker')
        sudo('systemctl start docker')
        sudo('systemctl enable docker')
    return res

@task
def start_avinetworks_server():
    sudo('docker run -d -p 80:80 avinetworks/server')

@task
def start_ab(vip, n):
    run('killall ab')
    for i in xrange(0, n):
        run('nohup ab -r -c 100 -n 100000000 https://%s/ >& /dev/null < '
            '/dev/null &' % vip, pty=False)

@task
def stop_ab():
    run('killall ab')

class FabricException(Exception):
    pass

class gcp(Cloud):
    def __init__(self, cloud, log):
        super(gcp, self).__init__(cloud, log)
        credentials = GoogleCredentials.get_application_default()
        self.compute = discovery.build('compute', 'v1', credentials=credentials)
        fabric_env.warn_only = True
        fabric_env.use_shell = False
        fabric_env.skip_bad_hosts = True
        fabric_env.linewise = True
        fabric_env.connection_attempts = 3
        fabric_env.command_timeout = 600
        fabric_env.keepalive = 30
        fabric_env.timeout = 30 # Access to any host should not take more than 30 seconds
        fabric_env.abort_exception = FabricException
        fabric_env.abort_on_prompts = True
        fabric_env.port = 22
        fabric_env.user = cloud['clouddata']['ssh_username']
        ssh_key_file = os.path.expanduser('~') + '/gcp_key'
        if not os.path.isfile(ssh_key_file):
            with open(ssh_key_file, 'w') as f:
                f.write(cloud['clouddata']['ssh_private_key'])
        os.chmod(ssh_key_file, 0400)
        fabric_env.key_filename = ssh_key_file
        fabric_output['everything'] = False
        fabric_output['exceptions'] = False
        fabric_output['status'] = False
        fabric_output['aborts'] = False
        fabric_env.parallel = True
        self.target_num_instances = 0

    def wait_for_operation(self, zone, project, operation):
        self.log.info('Waiting for operation %s to finish...' % operation)
        tries = 0
        while True:
            tries = tries + 1
            if tries > 50:
                self.log.warn('Abandoing operation %s', operation)
                return

            try:
                result = self.compute.zoneOperations().get(
                    project=project,
                    zone=zone,
                    operation=operation['name']).execute()
            except Exception:
                self.log.warn('Exception %s getting op %s' % 
                              (traceback.format_exc(), operation))
                time.sleep(4)
                continue

            if result['status'] == 'DONE':
                self.log.info("operation %s DONE." % operation)
                if 'error' in result:
                    self.log.error('operation %s error %s' % (operation['name'],
                        result['error']))
                return

            self.log.info('operation %s status %s' % (operation['name'], 
                                                      result['status']))
            time.sleep(4)

    def _create_instance_config(self, name, inst_info):
        zone = inst_info.get('zone', 'us-central1-b')
        inst_type = inst_info.get('instance_type', 'n1-standard-1')
        subnet = inst_info['subnet']
        canIpForward = inst_info.get('canIpForward', False)
        diskSizeGb = inst_info.get('diskSizeGb', 40)
        diskMode = inst_info.get('diskMode', 'READ_WRITE')
        diskType = inst_info.get('diskType', 'PERSISTENT')
        preemptible = inst_info.get('preemptible', False)
        scope = inst_info.get('scope', 'https://www.googleapis.com/auth/compute.readonly')
        tags = inst_info.get('tags', [])
        image_project = inst_info.get('image_project', 'centos-cloud')
        image_name = inst_info.get('image_name', '')
        if not image_name:
            image_family = inst_info.get('image_family', 'centos-7')
            image_response = self.compute.images().getFromFamily(
                project=image_project, family=image_family).execute()
        else:
            image_response = self.compute.images().get(
                project=image_project, image=image_name).execute()
        source_disk_image = image_response['selfLink']
        external_access = inst_info.get('external_access', True)

        # Configure the machine
        machine_type = 'zones/%s/machineTypes/%s' % (zone, inst_type)

        config = {
            'name': name,
            'machineType': machine_type,

            # Specify the boot disk and the image to use as a source.
            'disks': [
                {
                    'boot': True,
                    'autoDelete': True,
                    'mode': diskMode,
                    'type': diskType,
                    'initializeParams': {
                        'sourceImage': source_disk_image,
                        'diskSizeGb': diskSizeGb,
                    }
                }
            ],

            'canIpForward': canIpForward,

            # Allow the instance to access cloud storage and logging.
            'serviceAccounts': [{
                'email': 'default',
                'scopes': [
                    scope
                ]
            }],
        }

        # Specify a network interface with NAT to access the public
        # internet if external_access is set
        if external_access:
            config['networkInterfaces'] = [{
                'subnetwork': subnet,
                'accessConfigs': [{'name': 'external-nat', 'type': 'ONE_TO_ONE_NAT'}]
            }]
        else:
            config['networkInterfaces'] = [{
                'subnetwork': subnet
            }]

        if preemptible:
            config['scheduling'] = {'preemptible': True}

        if tags:
            config['tags'] = tags

        return config

    def create_instance(self, name, inst_info, ssh_user, ssh_key, wait=False):
        zone = inst_info.get('zone', 'us-central1-b')
        project = inst_info['project']

        config = self._create_instance_config(name, inst_info)

        try:
            operation = self.compute.instances().insert(project=project,
                zone=zone, body=config).execute()
        except Exception:
            self.log.error('Exception creating instance %s %s' % (name,
                traceback.format_exc()))
            return None

        self.log.info('Instance %s created operation %s. Waiting for '
            'completion' % (name, operation))

        if wait:
            self.wait_for_operation(zone, project, operation)
        else:
            time.sleep(5)

        # ssh_key is of the form ssh-rsa key user@machine
        ssh_key_list = ssh_key.split()

        try:
            inst = self.compute.instances().get(project=project, zone=zone,
                instance=name).execute()
        except Exception:
            self.log.error('Exception getting instance %s %s' % (name,
                traceback.format_exc()))
            return None

        fingerprint = inst['metadata']['fingerprint']
        items = inst['metadata'].get('items', [])
        added = False
        for item in items:
            if item['key'] == 'ssh-keys':
                # value is of the form 'user:ssh-rsa key user\nuser:ssh-rsa key user'
                keys = item['value'].splitlines()
                for k in keys:
                    k_list = k.split()
                    if k_list[1] == ssh_key_list[1] and k_list[2] == ssh_user:
                        self.log.info('key for user %s found already' % ssh_user)
                        added = True
                        break
                if not added:
                    item['value'] = item['value'] + '\n%s:ssh-rsa %s %s' % \
                        (ssh_user, ssh_key_list[1], ssh_user)
                    added = True

        if not added:
            items.append({'key': 'ssh-keys', 'value': '%s:ssh-rsa %s %s' % \
                        (ssh_user, ssh_key_list[1], ssh_user)})
                    
        body = {'kind': 'compute#metadata', 'fingerprint': fingerprint,
            'items': items}

        try:
            operation = self.compute.instances().setMetadata(project=project, 
                zone=zone, instance=name, body=body).execute()
        except Exception:
            self.log.error('Exception setMetadata instance %s %s' % (name,
                traceback.format_exc()))

        if wait:
            self.wait_for_operation(zone, project, operation)

        return operation

    def _create_instances_sync(self, inst_info, prefix, num_instances, 
                               ssh_username, ssh_key):
        instances = self.list_instances(inst_info)
        prefix_instances = [i for i in instances if i['name'].startswith(prefix)]

        if len(prefix_instances) >= num_instances:
            self.log.info('Curr running instances %d fulfil target instances '
                          '%d' % (len(prefix_instances), num_instances))
            return prefix_instances

        prefix_instances = sorted(prefix_instances, key=lambda i: \
                        int(i['name'].split(prefix)[1]), reverse=True)
        to_create = num_instances - len(prefix_instances)
        last_inst_num = int(prefix_instances[0]['name'].split(prefix)[1]) if \
                    prefix_instances else 0
        ops = []
        while to_create > 0:
            last_inst_num = last_inst_num + 1
            name = '%s%s' % (prefix, last_inst_num)
            self.log.info('Creating instance %s' % name)
            op = self.create_instance(name, inst_info, ssh_username, ssh_key)
            ops.append(op)
            to_create = to_create - 1

        zone = inst_info.get('zone', 'us-central1-b')
        project = inst_info['project']
        for op in ops:
            if op:
                self.wait_for_operation(zone, project, op)

        instances = self.list_instances(inst_info)
        prefix_instances = [i for i in instances if i['name'].startswith(prefix)]

        self.log.info('%s instances prefix %s created', len(prefix_instances),
                      prefix)

        return prefix_instances

    def _gcp_alloc_batch(self, gcp, cb):
        batch = None
        try:
            batch = gcp.new_batch_http_request(callback=cb)
        except:
            self.log.error('new_batch_http_request returned exception %s',
                           traceback.format_exc())
        if not batch:
            self.log.error('new_batch_http_request failed')

        return batch

    def _create_instance_cb(self, request_str, response, exception):
        if self.target_num_instances > 0:
            self.target_num_instances = self.target_num_instances - 1
        if self.target_num_instances == 0:
            self.log.info('All instances complete!')
        else:
            self.log.info('%s instances left', self.target_num_instances)

        if exception:
            self.log.error('Route add CB error %s %s', request_str, exception)
            return

        self.log.warn('Response %s', response)

        # request_str is a json encoded str
        try:
            mdata = json.loads(request_str)
        except Exception:
            self.log.warn('Unable to decode mdata %s', request_str)
            return

        self.log.info('Instance %s created', mdata['name'])
        # ssh_key is of the form ssh-rsa key user@machine
        ssh_key_list = mdata['create_ssh_key'].split()

        try:
            inst = self.compute.instances().get(project=mdata['project'], 
                zone=mdata['zone'], instance=mdata['name']).execute()
        except Exception:
            self.log.error('Exception getting instance %s %s' % (mdata['name'],
                traceback.format_exc()))
            return None

        fingerprint = inst['metadata']['fingerprint']
        items = inst['metadata'].get('items', [])
        added = False
        for item in items:
            if item['key'] == 'ssh-keys':
                # value is of the form 'user:ssh-rsa key user\nuser:ssh-rsa key user'
                keys = item['value'].splitlines()
                for k in keys:
                    k_list = k.split()
                    if (k_list[1] == ssh_key_list[1] and 
                        k_list[2] == mdata['ssh_user']):
                        self.log.info('key for user %s found already' % 
                            mdata['ssh_user'])
                        added = True
                        break
                if not added:
                    item['value'] = item['value'] + '\n%s:ssh-rsa %s %s' % \
                        (mdata['ssh_user'], ssh_key_list[1], mdata['ssh_user'])
                    added = True

        if not added:
            items.append({'key': 'ssh-keys', 'value': '%s:ssh-rsa %s %s' % \
                        (mdata['ssh_user'], ssh_key_list[1], mdata['ssh_user'])})
                    
        body = {'kind': 'compute#metadata', 'fingerprint': fingerprint,
            'items': items}

        try:
            self.compute.instances().setMetadata(project=
                mdata['project'], zone=mdata['zone'], instance=mdata['name'], 
                body=body).execute()
        except Exception:
            self.log.error('Exception setMetadata instance %s %s' % 
                           (mdata['name'], traceback.format_exc()))

    def _create_instances_async(self, inst_info, prefix, num_instances, 
                                ssh_username, ssh_key):
        zone = inst_info.get('zone', 'us-central1-b')
        project = inst_info['project']
        instances = self.list_instances(inst_info)
        prefix_instances = [i for i in instances if i['name'].startswith(prefix)]

        if len(prefix_instances) >= num_instances:
            self.log.info('Curr running instances %d fulfil target instances '
                          '%d' % (len(prefix_instances), num_instances))
            return prefix_instances

        prefix_instances = sorted(prefix_instances, key=lambda i: \
                        int(i['name'].split(prefix)[1]), reverse=True)
        to_create = num_instances - len(prefix_instances)
        self.target_num_instances = to_create
        last_inst_num = int(prefix_instances[0]['name'].split(prefix)[1]) if \
                    prefix_instances else 0

        batch = self._gcp_alloc_batch(self.compute, self._create_instance_cb)
        if not batch:
            self.log.warn('unable to allocate batch')
            return prefix_instances

        while to_create > 0:
            last_inst_num = last_inst_num + 1
            name = '%s%s' % (prefix, last_inst_num)
            self.log.info('Creating instance %s' % name)
            config = self._create_instance_config(name, inst_info)
            mdata = {'create_ssh_key': ssh_key, 'name': name, 'zone': zone,
                'project': project, 'ssh_user': ssh_username}
            try:
                batch.add(self.compute.instances().insert(project=project,
                    zone=zone, body=config), request_id=json.dumps(mdata))
            except Exception:
                self.log.warn('Unable to add create instance for %s exc %s',
                              name, traceback.format_exc())
            to_create = to_create - 1

        for retry in range(5):
            try:
                batch.execute()
                break
            except:
                self.log.error('gcp: batch execute failed retry %d %s', retry, 
                    traceback.format_exc())
                time.sleep(3)
        
        instances = self.list_instances(inst_info)
        prefix_instances = [i for i in instances if i['name'].startswith(prefix)]

        self.log.info('%s instances prefix %s created', len(prefix_instances),
                      prefix)

        return prefix_instances

    def create_client(self, inst_info, prefix, num_instances, ssh_username,
                         ssh_key):
        if inst_info.get('yum_install', False):
            insts = self._create_instances_sync(inst_info, prefix, num_instances, 
                                      ssh_username, ssh_key)
            ips = [i['info']['networkInterfaces'][0]['networkIP'] for i in insts]
            image = inst_info.get('image_name',
                        inst_info.get('image_family', 'centos-7'))
            self.log.info('Starting install_ab_task on %s IPs %s', len(ips), ips)
            num_ips = len(ips)
            start = 0
            while True:
                end = start + 10 if start+10 < num_ips else num_ips-1
                self.log.info('Processing 10 ips %s', ips[start:end])
                self._run_task(ips[start:end], install_ab_task, image)
                start = start + 10
                if end == num_ips-1:
                    break
        else:
            insts = self._create_instances_async(inst_info, prefix, num_instances, 
                                      ssh_username, ssh_key)
        return insts

    def start_test(self, inst_info, vip, prefix, num_instances):
        instances = self.list_instances(inst_info)
        ii = [i for i in instances if i['name'].startswith(prefix)]
        if len(ii) < num_instances:
            self.log.warn('Just %d instances running %d requested' % 
                          (len(ii), num_instances))
        ips = {i['info']['networkInterfaces'][0]['networkIP'] for i in ii}
        self._run_task(ips, start_ab, vip, inst_info['client_threads'])

    def stop_test(self, inst_info, prefix):
        instances = self.list_instances(inst_info)
        ii = [i for i in instances if i['name'].startswith(prefix)]
        ips = {i['info']['networkInterfaces'][0]['networkIP'] for i in ii}
        self._run_task(ips, stop_ab)

    def _run_task(self, inst_ips, task, *args, **kwargs):
        hosts = list(inst_ips)
        with settings(parallel=True, pool_size=32):
            try:
                result = execute(task, hosts=hosts, *args, **kwargs)
                self.log.info('se_ssh res %s', result)
            except Exception as e:
                self.log.warn('Failed to execute task for hosts %s: %s' %
                              (hosts, e))

    def _create_ses(self, api, cloud, inst_ips):
        cloud_obj = api.get_object_by_name('cloud', cloud)
        if not cloud_obj:
            self.log.warn('Unable to retrieve cloud %s' % cloud)
            return 0
        if 'linuxserver_configuration' not in cloud_obj:
            cloud_obj['linuxserver_configuration'] = {}
        for h in cloud_obj['linuxserver_configuration'].get('hosts', []):
            if h['host_ip']['addr'] in inst_ips:
                inst_ips.discard(h['host_ip']['addr'])
                self.log.info('Skipping inst %s' % h['host_ip']['addr'])
        if 'hosts' not in cloud_obj['linuxserver_configuration']:
            cloud_obj['linuxserver_configuration']['hosts'] = []
        for inst in inst_ips:
            h = {'host_ip': {'addr': inst, 'type': 'V4'}, 'host_attr': 
                [{u'attr_key': u'CPU', u'attr_val': u'All'}, 
                {u'attr_key': u'MEMORY', u'attr_val': u'All'}, 
                {u'attr_key': u'DPDK', u'attr_val': u'No'}, 
                {u'attr_key': u'SE_INBAND_MGMT', u'attr_val': u'False'}]}
            cloud_obj['linuxserver_configuration']['hosts'].append(h)
        if inst_ips:
            put_rsp = api.put('cloud/%s' % cloud_obj['uuid'], data=cloud_obj)
            self.log.info('Updated Cloud obj status %d %s' % 
                          (put_rsp.status_code, put_rsp.text))

    def create_ses(self, se_inst_info, ctrlr_inst_info, prefix, num_instances, 
                   ssh_username, ssh_public_key, ssh_private_key):
        ii = self._create_instances_sync(se_inst_info, prefix, num_instances, 
                                      ssh_username, ssh_public_key)
        if len(ii) < num_instances:
            self.log.warn('Just %d instances running %d requested' % 
                          (len(ii), num_instances))
            return 0
        self.log.info('Starting SE creation %s prefix %d instances' %
                      (prefix, num_instances))
        tenant = ctrlr_inst_info.get('tenant', 'admin')
        cloud = ctrlr_inst_info.get('cloud', 'Default-Cloud')
        avi_api = ApiSession.get_session(ctrlr_inst_info['api_endpoint'], 
            ctrlr_inst_info['username'], ctrlr_inst_info['password'], 
            tenant=tenant)
        ips = {i['info']['networkInterfaces'][0]['networkIP'] for i in ii}
        if se_inst_info.get('yum_install', False):
            image = se_inst_info.get('image_name', se_inst_info.get(
                                                'image_family', 'centos-7'))
            self._run_task(ips, install_docker_task, image)
        self._create_ses(avi_api, cloud, ips)
        up_ses = set()
        for i in xrange(0, 60):
            ses = avi_api.get('serviceengine')
            if ses.status_code != 200:
                self.log.warn('Unable to retrive SEs from %s' % 
                              ctrlr_inst_info['api_endpoint'])
                break
            se_objs = json.loads(ses.text)
            target = False
            for se in se_objs['results']:
                if 'oper_status' not in se:
                    time.sleep(5)
                elif se['oper_status']['state'] != 'OPER_UP':
                    self.log.info('SE %s oper status %s not up' %
                                  (se['name'], se['oper_status']))
                    time.sleep(5)
                else:
                    # name is of the form 10.70.119.35--se--dark-lake-qaa7v
                    l = se['name'].split('--se--')
                    up_ses.add(l[0])
                if len(up_ses) >= num_instances:
                    target = True
                    break
            if target:
                break
            self.log.info('SE targets not reached yet %d SEs retrieved' %
                          len(se_objs['results']))
            time.sleep(5)
        self.log.info('%d SEs created' % len(up_ses))
        return len(up_ses)

    def create_pool(self, pool_inst_info, prefix, num_instances, 
                   ssh_username, ssh_public_key):
        ii = self._create_instances_sync(pool_inst_info, prefix, num_instances, 
                                      ssh_username, ssh_public_key)
        if len(ii) < num_instances:
            self.log.warn('Just %d instances running %d requested' % 
                          (len(ii), num_instances))
            return 0
        ips = {i['info']['networkInterfaces'][0]['networkIP'] for i in ii}
        if pool_inst_info.get('yum_install', False):
            image = pool_inst_info.get('image_name', 
                        pool_inst_info.get('image_family', 'centos-7'))
            self._run_task(ips, install_docker_task, image)
        self._run_task(ips, start_avinetworks_server)

    def _delete_cc_config_ses(self, ctrlr_inst_info, avi_api, cloud_obj):
        cloud_obj['linuxserver_configuration']['hosts'] = []
        put_rsp = avi_api.put('cloud/%s' % cloud_obj['uuid'], data=cloud_obj)
        self.log.info('Updated Cloud obj status %d %s' % 
                          (put_rsp.status_code, put_rsp.text))
        for i in xrange(0, 60):
            ses = avi_api.get('serviceengine')
            if ses.status_code != 200:
                self.log.warn('Unable to retrive SEs from %s' % 
                              ctrlr_inst_info['api_endpoint'])
                return
            se_objs = json.loads(ses.text)
            if len(se_objs['results']) > 0:
                self.log.info('%d SEs remain' % (len(se_objs['results'])))
                time.sleep(5)
            else:
                break

    def delete_ses(self, se_inst_info, ctrlr_inst_info, prefix):
        tenant = ctrlr_inst_info.get('tenant', 'admin')
        cloud = ctrlr_inst_info.get('cloud', 'Default-Cloud')
        avi_api = ApiSession.get_session(ctrlr_inst_info['api_endpoint'], 
            ctrlr_inst_info['username'], ctrlr_inst_info['password'], 
            tenant=tenant)
        cloud_obj = avi_api.get_object_by_name('cloud', cloud)
        if not cloud_obj:
            self.log.warn('Unable to retrieve cloud %s' % cloud)
            return
        if ('linuxserver_configuration' in cloud_obj and 
                cloud_obj['linuxserver_configuration'].get('hosts', [])):
            self._delete_cc_config_ses(ctrlr_inst_info, avi_api, cloud_obj)
        self.delete_instances(se_inst_info, prefix)

    def list_instances(self, inst_info):
        zone = inst_info.get('zone', 'us-central1-b')
        project = inst_info['project']
        try:
            insts = self.compute.instances().list(project=project, 
                                                 zone=zone).execute()
        except Exception:
            self.log.error('Exception listing instances %s' % 
                traceback.format_exc())
            return []

        inst_list = []
        for i in insts['items']:
            if i['status'] == 'RUNNING':
                inst_list.append({'name': i['name'], 'info': i})
        return inst_list

    def delete_instance(self, inst_info, name, wait=False):
        zone = inst_info.get('zone', 'us-central1-b')
        project = inst_info['project']
        operation = self.compute.instances().delete(project=project, zone=zone,
            instance=name).execute()

        if wait:
            self.wait_for_operation(zone, project, operation)

        return operation

    def delete_instances(self, inst_info, prefix, wait=False):
        instances = self.list_instances(inst_info)
        zone = inst_info.get('zone', 'us-central1-b')
        project = inst_info['project']

        ops = []
        for i in instances:
            if i['name'].startswith(prefix):
                self.log.info('Deleting instance %s' % i['name'])
                op = self.delete_instance(inst_info, i['name'], wait)
                ops.append(op)

        if wait:
            for op in ops:
                if op:
                    self.wait_for_operation(zone, project, op)

    def _create_cc_user(self, avi_api, ssh_user, ssh_pub_key, ssh_priv_key):
        cc_user_obj = {'name': ssh_user, 'public_key': ssh_pub_key,
            'private_key': ssh_priv_key}
        resp = avi_api.post('cloudconnectoruser', data=cc_user_obj)
        self.log.info('Created cc user %s rsp %s' % (cc_user_obj, resp.text))
        if resp.status_code >= 300:
            self.log.warn('Error creating cc user obj %s' % resp.status_code)
            return None
        return avi_api.get_object_by_name('cloudconnectoruser', ssh_user)

    def create_cloud(self, ctrlr_inst_info, se_inst_info,
                 ssh_user, ssh_pub_key, ssh_priv_key):
        tenant = ctrlr_inst_info.get('tenant', 'admin')
        avi_api = ApiSession.get_session(ctrlr_inst_info['api_endpoint'], 
            ctrlr_inst_info['username'], ctrlr_inst_info['password'], 
            tenant=tenant)

        resp = avi_api.get('seproperties')
        if resp.status_code != 200:
            self.log.warn('Error getting seproperties %d' % resp.status_code)
            return
        se_prop_obj = json.loads(resp.text)
        se_prop_obj['se_runtime_properties']['se_handle_interface_routes'] = True
        se_prop_obj['se_runtime_properties']['global_mtu'] = 1400
        put_rsp = avi_api.put('seproperties/%s' % se_prop_obj['uuid'], data=se_prop_obj)
        self.log.info('Updated seproperties obj status %d %s' % 
                      (put_rsp.status_code, put_rsp.text))

        net_obj_name = ctrlr_inst_info.get('network', 'perf-network')
        net_obj = avi_api.get_object_by_name('network', net_obj_name)
        if not net_obj:
            ipam_subnet = ctrlr_inst_info['ipam_subnet'].split('/')
            prefix = ipam_subnet[0]
            mask = ipam_subnet[1]
            net_obj = {'name': net_obj_name, 'configured_subnets': 
                [{'prefix': {'ip_addr': {'addr': prefix, 'type': 'V4'},
                'mask': mask}, 'static_ranges': [{'begin':
                {'addr': ctrlr_inst_info['ipam_start'], 'type': 'V4'},
                'end': {'addr': ctrlr_inst_info['ipam_start'], 'type': 'V4'}}]}]}
            resp = avi_api.post('network', data=net_obj)
            self.log.info('Created network %s rsp %s' % (net_obj, resp.text))
            if resp.status_code >= 300:
                self.log.warn('Error creating network obj %s' % resp.status_code)
                return
            net_obj = avi_api.get_object_by_name('network', net_obj_name)

        ipam_obj_name = ctrlr_inst_info.get('ipamdnsproviderprofile', 'perf-ipam')
        ipam_obj = avi_api.get_object_by_name('ipamdnsproviderprofile', 
                                              ipam_obj_name)
        if not ipam_obj:
            ipam_obj = {'name': ipam_obj_name, 'type': 'IPAMDNS_TYPE_GCP',
                'gcp_profile': {'usable_network_refs': [net_obj['url']]}}
            resp = avi_api.post('ipamdnsproviderprofile', data=ipam_obj)
            self.log.info('Created GCP IPAM %s rsp %s' % (ipam_obj, resp.text))
            if resp.status_code >= 300:
                self.log.warn('Error creating ipam obj %s' % resp.status_code)
                return
            ipam_obj = avi_api.get_object_by_name('ipamdnsproviderprofile', 
                                                  ipam_obj_name)

        cc_user = avi_api.get_object_by_name('cloudconnectoruser', ssh_user)
        if not cc_user:
            cc_user = self._create_cc_user(avi_api, ssh_user, ssh_pub_key, 
                                           ssh_priv_key)
            if not cc_user:
                return

        cloud = ctrlr_inst_info.get('cloud', 'Default-Cloud')
        cloud_obj = avi_api.get_object_by_name('cloud', cloud)
        if not cloud_obj:
            cloud_obj = {'vtype': 'CLOUD_LINUXSERVER', 'ipam_provider_ref':
                ipam_obj['url'], 'linuxserver_configuration': {'ssh_attr':
                    {'ssh_user': ssh_user}}}
            resp = avi_api.post('cloud', data=cloud_obj)
            self.log.info('Created cloud %s rsp %s' % (cloud_obj, resp.text))
            if resp.status_code >= 300:
                self.log.warn('Error creating cloud obj %s' % resp.status_code)
                return
        else:
            cloud_obj['vtype'] = 'CLOUD_LINUXSERVER'
            cloud_obj['ipam_provider_ref'] = ipam_obj['url']
            if 'linuxserver_configuration' not in cloud_obj:
                cloud_obj['linuxserver_configuration'] = {}
            cloud_obj['linuxserver_configuration']['ssh_attr'] = \
                    {'ssh_user': ssh_user}
            put_rsp = avi_api.put('cloud/%s' % cloud_obj['uuid'], data=cloud_obj)
            self.log.info('Updated Cloud obj status %d %s' % 
                          (put_rsp.status_code, put_rsp.text))

        seg_objs_rsp = avi_api.get('serviceenginegroup')
        seg_objs = json.loads(seg_objs_rsp.text)
        seg_obj = next((seg for seg in seg_objs['results'] 
                        if cloud_obj['uuid'] in seg['cloud_ref']), None)
        if not seg_obj:
            self.log.warn('Unable to find SEGroup obj for cloud %s',
                          cloud_obj['uuid'])
            return
        seg_obj['min_scaleout_per_vs'] = se_inst_info['instances']
        seg_obj['max_scaleout_per_vs'] = se_inst_info['instances']
        seg_obj['dedicated_dispatcher_core'] = True
        put_rsp = avi_api.put('serviceenginegroup/%s' % seg_obj['uuid'], 
                              data=seg_obj)
        self.log.info('Updated SEGroup obj status %d %s' % 
                          (put_rsp.status_code, put_rsp.text))

    def create_vs(self, ctrlr_inst_info, pool_inst_info, 
                 pool_prefix, num_pool_instances):
        instances = self.list_instances(pool_inst_info)
        pool_instances = [i for i in instances if i['name'].startswith(pool_prefix)]

        if len(pool_instances) < num_pool_instances:
            self.log.warn('Just %d instances running %d requested' % 
                          (len(pool_instances), num_pool_instances))
            return
        pool_ips = {i['info']['networkInterfaces'][0]['networkIP'] 
            for i in pool_instances}

        tenant = ctrlr_inst_info.get('tenant', 'admin')
        avi_api = ApiSession.get_session(ctrlr_inst_info['api_endpoint'], 
            ctrlr_inst_info['username'], ctrlr_inst_info['password'], 
            tenant=tenant)

        ds_name = ctrlr_inst_info.get('datascript', 'perf-vs-datascript')
        ds_obj = avi_api.get_object_by_name('vsdatascriptset', ds_name)
        if not ds_obj:
            ds_obj = {'name': ds_name, 'datascript': [{'evt': 
                'VS_DATASCRIPT_EVT_HTTP_REQ', 'script': 'avi.http.response(200)'}]}
            resp = avi_api.post('vsdatascriptset', data=ds_obj)
            self.log.info('Created DataScript %s rsp %s' % (ds_obj, resp.text))
            if resp.status_code >= 300:
                self.log.warn('Error creating DataScript obj %s' % resp.status_code)
                return
        else:
            ds_obj['name'] = ds_name
            ds_obj['datascript'] = [{'evt': 'VS_DATASCRIPT_EVT_HTTP_REQ', \
                'script': 'avi.http.response(200)'}]
            put_rsp = avi_api.put('vsdatascriptset/%s' % ds_obj['uuid'], 
                              data=ds_obj)
            self.log.info('Updated DataScript obj status %d %s' % 
                          (put_rsp.status_code, put_rsp.text))
        ds_obj = avi_api.get_object_by_name('vsdatascriptset', ds_name)

        sslcert_name = ctrlr_inst_info.get('ssl_cert', None)
        if sslcert_name:
            sslcert = avi_api.get_object_by_name('sslkeyandcertificate',
                                             sslcert_name)
            if not sslcert:
                self.log.warn('SSL cert %s not found', sslcert_name)
                return

        pool_name = pool_inst_info.get('name', 'perf-pool')
        pool_obj = avi_api.get_object_by_name('pool', pool_name)
        servers = [{'ip': {'type': 'V4', 'addr': i}, 'port': 80} \
                  for i in pool_ips]
        if not pool_obj:
            pool_obj = {'name': pool_name, 'servers': servers}
            resp = avi_api.post('pool', data=pool_obj)
            self.log.info('Created Pool %s rsp %s' % (pool_obj, resp.text))
            if resp.status_code >= 300:
                self.log.warn('Error creating Pool obj %s' % resp.status_code)
                return
        else:
            pool_obj['servers'] = servers
            put_rsp = avi_api.put('pool/%s' % pool_obj['uuid'], 
                              data=pool_obj)
            self.log.info('Updated pool obj status %d %s' % 
                          (put_rsp.status_code, put_rsp.text))
        pool_obj = avi_api.get_object_by_name('pool', pool_name)

        vs_name = ctrlr_inst_info.get('virtualservice', 'perf-vs')
        vs_obj = avi_api.get_object_by_name('virtualservice', vs_name)
        if sslcert_name:
            service = {'port': ctrlr_inst_info['port'], 'enable_ssl': True}
        else:
            service = {'port': ctrlr_inst_info['port'], 'enable_ssl': False}
        analytics_policy = {'client_insights': 'NO_INSIGHTS',
                'metrics_realtime_update': {'duration': 0, 'enabled': True},
                'full_client_logs': {'duration': 0, 'enabled': False}}
        placement_subnet_l = ctrlr_inst_info['placement_subnet'].split('/')
        placement_subnet = {'ip_addr': {'addr': placement_subnet_l[0], 
            'type': 'V4'}, 'mask': placement_subnet_l[1]}
        if not vs_obj:
            vs_obj = {'name': vs_name, 'ip_address': {'addr': 
                ctrlr_inst_info['vip'], 'type': 'V4'}, 'services':
                [service], 'analytics_policy': analytics_policy,
                'scaleout_ecmp': True, 'vs_datascripts': [{'index': 1,
                'vs_datascript_set_ref': ds_obj['url']}], 'pool_ref': 
                pool_obj['url'], 'subnet': placement_subnet, 
                'ign_pool_net_reach': True}
            if sslcert_name:
                vs_obj['ssl_key_and_certificate_refs'] = [sslcert['url']]
            resp = avi_api.post('virtualservice', data=vs_obj)
            self.log.info('Created VirtualService %s rsp %s' % (vs_obj, resp.text))
            if resp.status_code >= 300:
                self.log.warn('Error creating VirtualService obj %s' % resp.status_code)
                return
        else:
            vs_obj['ip_address'] = {'addr': ctrlr_inst_info['vip'], 'type': 'V4'}
            vs_obj['services'] = [service]
            vs_obj['analytics_policy'] = analytics_policy
            vs_obj['scaleout_ecmp'] = True
            vs_obj['vs_datascripts'] = [{'index': 1, 'vs_datascript_set_ref': 
                ds_obj['url']}]
            if sslcert_name:
                vs_obj['ssl_key_and_certificate_refs'] = [sslcert['url']]
            vs_obj['pool_ref'] = pool_obj['url']
            vs_obj['subnet'] = placement_subnet
            vs_obj['ign_pool_net_reach'] = True
            put_rsp = avi_api.put('virtualservice/%s' % vs_obj['uuid'], 
                              data=vs_obj)
            self.log.info('Updated VirtualService obj status %d %s' % 
                          (put_rsp.status_code, put_rsp.text))

    def delete_vs(self, ctrlr_inst_info, pool_inst_info):
        tenant = ctrlr_inst_info.get('tenant', 'admin')
        avi_api = ApiSession.get_session(ctrlr_inst_info['api_endpoint'], 
            ctrlr_inst_info['username'], ctrlr_inst_info['password'], 
            tenant=tenant)

        vs_name = ctrlr_inst_info.get('virtualservice', 'perf-vs')
        try:
            rsp = avi_api.delete_by_name('virtualservice', vs_name)
        except:
            pass
        else:
            self.log.info('Delete rsp %s', rsp)
        pool_name = pool_inst_info.get('name', 'perf-pool')
        try:
            rsp = avi_api.delete_by_name('pool', pool_name)
        except:
            pass
        else:
            self.log.info('Delete rsp %s', rsp)
        ds_name = ctrlr_inst_info.get('datascript', 'perf-vs-datascript')
        try:
            rsp = avi_api.delete_by_name('vsdatascriptset', ds_name)
        except:
            pass
        else:
            self.log.info('Delete rsp %s', rsp)

    def delete_cloud(self, ctrlr_inst_info, ssh_user):
        tenant = ctrlr_inst_info.get('tenant', 'admin')
        avi_api = ApiSession.get_session(ctrlr_inst_info['api_endpoint'], 
            ctrlr_inst_info['username'], ctrlr_inst_info['password'], 
            tenant=tenant)

        cloud = ctrlr_inst_info.get('cloud', 'Default-Cloud')
        cloud_obj = avi_api.get_object_by_name('cloud', cloud)
        if cloud_obj:
            if ('linuxserver_configuration' in cloud_obj and 
                cloud_obj['linuxserver_configuration'].get('hosts', [])):
                self._delete_cc_config_ses(ctrlr_inst_info, avi_api, cloud_obj)
            cloud_obj = avi_api.get_object_by_name('cloud', cloud)
            cloud_obj['type'] = 'CLOUD_NONE'
            cloud_obj.pop('ipam_provider_ref', None)
            cloud_obj.pop('linuxserver_configuration', None)
            put_rsp = avi_api.put('cloud/%s' % cloud_obj['uuid'], data=cloud_obj)
            self.log.info('Updated Cloud obj status %d %s' % 
                          (put_rsp.status_code, put_rsp.text))
        ipam_obj_name = ctrlr_inst_info.get('ipamdnsproviderprofile', 'perf-ipam')
        try:
            rsp = avi_api.delete_by_name('ipamdnsproviderprofile', ipam_obj_name)
        except:
            pass
        else:
            self.log.info('Delete rsp %s', rsp)
        net_obj_name = ctrlr_inst_info.get('network', 'perf-network')
        try:
            rsp = avi_api.delete_by_name('network', net_obj_name)
        except:
            pass
        else:
            self.log.info('Delete rsp %s', rsp)
        try:
            rsp = avi_api.delete_by_name('cloudconnectoruser', ssh_user)
        except:
            pass
        else:
            self.log.info('Delete rsp %s', rsp)
