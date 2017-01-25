class Cloud(object):
    def __init__(self, cloud, log):
        self.log = log
        self.cloud = cloud

    def create_instance(self, name, inst_info, ssh_user, ssh_key, wait=False):
        pass

    def create_client(self, inst_info, prefix, num_instances, ssh_username,
                                                      ssh_key):
        return 0

    def create_pool(self, inst_info, prefix, num_instances, ssh_username,
                                                      ssh_key):
        return 0

    def create_ses(self, se_inst_info, ctrlr_inst_info, prefix, num_instances, 
                   ssh_username, ssh_public_key, ssh_private_key):
        return 0

    def start_test(self, inst_info, vip, prefix, num_instances):
        return

    def stop_test(self, inst_info, prefix):
        return

    def delete_ses(self, se_inst_info, ctrlr_inst_info, prefix):
        return 0

    def delete_instance(self, inst_info, name, wait=False):
        pass

    def delete_instances(self, inst_info, prefix, wait=False):
        pass

    def list_instances(self, inst_info):
        pass

    def wait_for_operation(self, inst_info, op):
        pass

    def create_vs(self, ctrlr_inst_info, pool_inst_info, pool_prefix, 
                  n_pool_instances):
        pass

    def delete_vs(self, ctrlr_inst_info, pool_inst_info):
        pass

    def create_cloud(self, ctrlr_inst_info, se_inst_info, ssh_user, 
                     ssh_pub_key, ssh_priv_key):
        pass

    def delete_cloud(self, ctrlr_inst_info, ssh_user):
        pass
