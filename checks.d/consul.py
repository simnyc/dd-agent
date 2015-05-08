# 3p
import requests

# project
from checks import AgentCheck

"""
Number of nodes in the cluster - /v1/catalog/nodes
Number of services in the cluster - /v1/catalog/services
Number of nodes in a given service - /v1/catalog/service/<service>
Number of services running per node - /v1/catalog/node/<node>
"""

class ConsulCheck(AgentCheck):
    CONSUL_CHECK = 'consul.up'
    HEALTH_CHECK = 'consul.check'

    CONSUL_CATALOG_CHECK = 'consul.catalog'
    CONSUL_NODE_CHECK = 'consul.node'

    def consul_request(self, instance, endpoint):
        url = "{}{}".format(instance.get('url'), endpoint)
        try:
            resp = requests.get(url)
        except requests.exceptions.Timeout:
            self.log.exception('Consul request to {0} timed out'.format(url))
            raise

        resp.raise_for_status()
        return resp.json()

    def should_check(self, instance):
        try:
            local_config = self.consul_request(instance, '/v1/agent/self')
            agent_addr = local_config.get('Config', {}).get('AdvertiseAddr')
            agent_port = local_config.get('Config', {}).get('Ports', {}).get('Server')
            agent_url = "{}:{}".format(agent_addr, agent_port)


            agent_dc = local_config.get('Config', {}).get('Datacenter')
            self.agent_dc = agent_dc

            leader = self.consul_request(instance, '/v1/status/leader')
            return agent_url == leader

        except Exception as e:
            return False

    # Problem these are all blocking HTTP requests and will, they should not be
    def get_services_in_cluster(self, instance):
        return self.consul_request(instance, '/v1/catalog/services')

    def get_nodes_in_cluster(self, instance):
        return self.consul_request(instance, '/v1/catalog/nodes')

    def get_nodes_with_service(self, instance, service, tag=None):
        if tag:
            consul_request_url = '/v1/catalog/service/{0}?tag={1}'.format(service,tag)
        else:
            consul_request_url = '/v1/catalog/service/{0}'.format(service)

        return self.consul_request(instance, consul_request_url)

    def get_services_on_node(self, instance, node):
        return self.consul_request(instance, '/v1/catalog/node/{0}'.format(node))

    def check(self, instance):
        if not self.should_check(instance):
            self.log.debug("Skipping check for this instance")
            return

        service_check_tags = ['consul_url:{}'.format(instance.get('url'))]
        perform_catalog_checks = instance.get('perform_catalog_checks',
                                              self.init_config.get('perform_catalog_checks'))

        try:
            health_state = self.consul_request(instance, '/v1/health/state/any')

            STATUS_SC = {
                'passing': AgentCheck.OK,
                'warning': AgentCheck.WARNING,
                'critical': AgentCheck.CRITICAL,
            }

            for check in health_state:
                status = STATUS_SC.get(check['Status'])
                if status is None:
                    continue

                tags = ["check:{}".format(check["CheckID"])]
                if check["ServiceName"]:
                    tags.append("service:{}".format(check["ServiceName"]))
                if check["ServiceID"]:
                    tags.append("service-id:{}".format(check["ServiceID"]))

            services = self.get_services_in_cluster(instance)
            self.service_check(self.HEALTH_CHECK, status, tags=tags)

        except Exception as e:
            self.service_check(self.CONSUL_CHECK, AgentCheck.CRITICAL,
                               tags=service_check_tags)
        else:
            self.service_check(self.CONSUL_CHECK, AgentCheck.OK,
                               tags=service_check_tags)

        if perform_catalog_checks:
            services = self.get_services_in_cluster(instance)
            main_tags = []

            if hasattr(self, 'agent_dc') and self.agent_dc:
                main_tags.append('consul_datacenter:{0}'.format(self.agent_dc))

            nodes_to_services = {}
            # Count all nodes providing each service
            # Tag them with the service name and service tags if they exist
            for service in services:
                nodes_with_service = self.get_nodes_with_service(instance, service)
                service_level_tags = main_tags + [ 'consul_service_id:{0}'.format(service) ]

                for n in nodes_with_service:
                    service_tags = n.get('ServiceTags') or []
                    all_tags = service_level_tags +\
                            [ 'consul_service_tag:{0}'.format(st) for st in service_tags]

                    # Allows to ask something like: How many Nodes provide a given Service?
                    self.count('consul.catalog.nodes_up', value=1, tags=all_tags)

                    # Store the services on this node, they'll be useful later
                    node_id = n.get('Node') or None
                    if not node_id:
                        continue

                    if node_id not in nodes_to_services:
                        nodes_to_services[node_id] = [ service ]
                    else:
                        nodes_to_services[node_id].append(service)

            for node, services in nodes_to_services.items():
                node_level_tags = main_tags + [ 'consul_node_id:{0}'.format(node) ]
                for service in services:
                    all_tags = node_level_tags + [ 'consul_service_id:{0}'.format(service) ]

                    # Allows to ask something like: How many Services are provided by a given node?
                    self.count('consul.catalog.services_up', value=1, tags=all_tags)
