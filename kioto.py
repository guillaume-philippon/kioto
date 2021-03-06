#!/usr/bin/env python2.7
"""Kubernetes cluster generator."""
__author__ = "Patrick Blaas <patrick@kite4fun.nl>"
__license__ = "GPL v3"
__version__ = "0.2.2"
__status__ = "Active"


import argparse
import httplib
import os
import subprocess
import base64
from jinja2 import Environment, FileSystemLoader

PATH = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_ENVIRONMENT = Environment(
    autoescape=False,
    loader=FileSystemLoader(os.path.join(PATH, '.')),
    trim_blocks=True)


# Testing if environment variables are available.
if not "OS_USERNAME" in os.environ:
    os.environ["OS_USERNAME"] = "Default"
if not "OS_PASSWORD" in os.environ:
    os.environ["OS_PASSWORD"] = "Default"
if not "OS_TENANT_NAME" in os.environ:
    os.environ["OS_TENANT_NAME"] = "Default"
if not "OS_TENANT_ID" in os.environ:
    os.environ["OS_TENANT_ID"] = "Default"
if not "OS_REGION_NAME" in os.environ:
    os.environ["OS_REGION_NAME"] = "Default"
if not "OS_AUTH_URL" in os.environ:
    os.environ["OS_AUTH_URL"] = "Default"

parser = argparse.ArgumentParser()
parser.add_argument("keypair", help="Keypair ID")
parser.add_argument("floatingip1", help="Floatingip 1 for API calls")
parser.add_argument("floatingip2", help="Floatingip 2 for public access to cluster")
parser.add_argument("--corepassword", help="Password to authenticate with core user")
parser.add_argument("--username", help="Openstack username - (OS_USERNAME environment variable)", default=os.environ["OS_USERNAME"])
parser.add_argument("--projectname", help="Openstack project Name - (OS_TENANT_NAME environment variable)", default=os.environ["OS_TENANT_NAME"])
parser.add_argument("--clustername", help="Clustername - (k8scluster)", default="k8scluster")
parser.add_argument("--subnetcidr", help="Private subnet CIDR - (192.168.3.0/24)", default="192.168.3.0/24")
parser.add_argument("--calicocidr", help="Calico subnet CIDR - (10.244.0.0/16)", default="10.244.0.0/16")
parser.add_argument("--managers", help="Number of k8s managers - (3)", type=int, default=3)
parser.add_argument("--workers", help="Number of k8s workers - (0)", type=int, default=0)
parser.add_argument("--managerimageflavor", help="Manager image flavor ID - (2004)", type=int, default=2004)
parser.add_argument("--workerimageflavor", help="Worker image flavor ID - (2008)", type=int, default=2008)
parser.add_argument("--dnsserver", help="DNS server - (8.8.8.8)", default="8.8.8.8")
parser.add_argument("--cloudprovider", help="Cloud provider support - (openstack)", default="openstack")
parser.add_argument("--k8sver", help="Hyperkube version - (v1.8.7_coreos.0)", default="v1.8.7_coreos.0")
parser.add_argument("--flannelver", help="Flannel image version - (v0.8.0)", default="v0.8.0")
parser.add_argument("--netoverlay", help="Network overlay - (flannel)", default="flannel")
parser.add_argument("--authmode", help="Authorization mode - (AlwaysAllow)", default="AlwaysAllow")
parser.add_argument("--alphafeatures", help="enable alpha feature - (false)", default="false")
args = parser.parse_args()

template = TEMPLATE_ENVIRONMENT.get_template('k8s.tf.tmpl')
calico_template = TEMPLATE_ENVIRONMENT.get_template('calico.yaml.tmpl')
cloudconf_template = TEMPLATE_ENVIRONMENT.get_template('k8scloudconf.yaml.tmpl')
kubeconfig_template = TEMPLATE_ENVIRONMENT.get_template('kubeconfig.sh.tmpl')
cloudconfig_template = TEMPLATE_ENVIRONMENT.get_template('cloud.conf.tmpl')
clusterstatus_template = TEMPLATE_ENVIRONMENT.get_template('cluster.status.tmpl')
opensslmanager_template = TEMPLATE_ENVIRONMENT.get_template('./tls/openssl.cnf.tmpl')
opensslworker_template = TEMPLATE_ENVIRONMENT.get_template('./tls/openssl-worker.cnf.tmpl')


try:
    #Create CA certificates

    def createCaCert():
        """Create CA certificates."""
        print("CA")
        subprocess.call(["openssl", "genrsa", "-out", "ca-key.pem", "2048"], cwd='./tls')
        subprocess.call(["openssl", "req", "-x509", "-new", "-nodes", "-key", "ca-key.pem", "-days", "10000", "-out", "ca.pem", "-subj", "/CN=k8s-ca"], cwd='./tls')

        print("etcd CA")
        subprocess.call(["openssl", "genrsa", "-out", "etcd-ca-key.pem", "2048"], cwd='./tls')
        subprocess.call(["openssl", "req", "-x509", "-new", "-nodes", "-key", "etcd-ca-key.pem", "-days", "10000", "-out", "etcd-ca.pem", "-subj", "/CN=etcd-k8s-ca"], cwd='./tls')

    def createSAcert():
        """Create Service Account certificates."""
        print("ServiceAcccount cert")

        openssltemplate = (opensslworker_template.render(
            ipaddress="127.0.0.1"
            ))

        with open('./tls/openssl.cnf', 'w') as openssl:
            openssl.write(openssltemplate)

        print("Service account K8s")
        subprocess.call(["openssl", "genrsa", "-out", "sa-"+(args.clustername)+"-k8s-key.pem", "2048"], cwd='./tls')
        subprocess.call(["openssl", "req", "-new", "-key", "sa-"+(args.clustername)+"-k8s-key.pem", "-out", "sa-"+(args.clustername)+"-k8s-key.csr", "-subj", "/CN=sa:k8s", "-config", "openssl.cnf"], cwd='./tls')
        subprocess.call(["openssl", "x509", "-req", "-in", "sa-"+(args.clustername)+"-k8s-key.csr", "-CA", "ca.pem", "-CAkey", "ca-key.pem", "-CAcreateserial", "-out", "sa-"+(args.clustername)+"-k8s.pem", "-days", "365", "-extensions", "v3_req", "-extfile", "openssl.cnf"], cwd='./tls')


    #Create node certificates
    def createNodeCert(nodeip, k8srole):
        """Create Node certificates."""
        print("received: " + nodeip)
        if k8srole == "manager":
            openssltemplate = (opensslmanager_template.render(
                floatingip1=args.floatingip1,
                ipaddress=nodeip,
                loadbalancer=(args.subnetcidr).rsplit('.', 1)[0]+".3"
                ))
        else:
            openssltemplate = (opensslworker_template.render(
                ipaddress=nodeip,
                ))

        with open('./tls/openssl.cnf', 'w') as openssl:
            openssl.write(openssltemplate)

        nodeoctet = nodeip.rsplit('.')[3]
        subprocess.call(["openssl", "genrsa", "-out", nodeip +"-k8s-node-key.pem", "2048"], cwd='./tls')
        subprocess.call(["openssl", "req", "-new", "-key", nodeip +"-k8s-node-key.pem", "-out", nodeip +"-k8s-node.csr", "-subj", "/CN=system:node:k8s-"+str(args.clustername)+"-node"+str(nodeoctet)+"/O=system:nodes", "-config", "openssl.cnf"], cwd='./tls')
        subprocess.call(["openssl", "x509", "-req", "-in", nodeip +"-k8s-node.csr", "-CA", "ca.pem", "-CAkey", "ca-key.pem", "-CAcreateserial", "-out", nodeip+"-k8s-node.pem", "-days", "365", "-extensions", "v3_req", "-extfile", "openssl.cnf"], cwd='./tls')

        # ${i}-etcd-worker.pem
        subprocess.call(["openssl", "genrsa", "-out", nodeip +"-etcd-node-key.pem", "2048"], cwd='./tls')
        subprocess.call(["openssl", "req", "-new", "-key", nodeip +"-etcd-node-key.pem", "-out", nodeip +"-etcd-node.csr", "-subj", "/CN="+nodeip+"-etcd-node", "-config", "openssl.cnf"], cwd='./tls')
        subprocess.call(["openssl", "x509", "-req", "-in", nodeip +"-etcd-node.csr", "-CA", "etcd-ca.pem", "-CAkey", "etcd-ca-key.pem", "-CAcreateserial", "-out", nodeip+"-etcd-node.pem", "-days", "365", "-extensions", "v3_req", "-extfile", "openssl.cnf"], cwd='./tls')


    def createClientCert(user):
        """Create Client certificates."""
        print("client: " + user)
        subprocess.call(["openssl", "genrsa", "-out", user +"-key.pem", "2048"], cwd='./tls')
        subprocess.call(["openssl", "req", "-new", "-key", user +"-key.pem", "-out", user+".csr", "-subj", "/CN="+user+"/O=system:masters", "-config", "openssl.cnf"], cwd='./tls')
        subprocess.call(["openssl", "x509", "-req", "-in", user+".csr", "-CA", "ca.pem", "-CAkey", "ca-key.pem", "-CAcreateserial", "-out", user+".pem", "-days", "365", "-extensions", "v3_req", "-extfile", "openssl.cnf"], cwd='./tls')

    def createCalicoObjects():
        """Create Calico cluster objects."""
        openssltemplate = (opensslworker_template.render(
            ipaddress="127.0.0.1"
            ))

        with open('./tls/openssl.cnf', 'w') as openssl:
            openssl.write(openssltemplate)

        print("Service account calico")
        subprocess.call(["openssl", "genrsa", "-out", "sa-"+(args.clustername)+"-calico-key.pem", "2048"], cwd='./tls')
        subprocess.call(["openssl", "req", "-new", "-key", "sa-"+(args.clustername)+"-calico-key.pem", "-out", "sa-"+(args.clustername)+"-calico-key.csr", "-subj", "/CN=sa:calico", "-config", "openssl.cnf"], cwd='./tls')
        subprocess.call(["openssl", "x509", "-req", "-in", "sa-"+(args.clustername)+"-calico-key.csr", "-CA", "etcd-ca.pem", "-CAkey", "etcd-ca-key.pem", "-CAcreateserial", "-out", "sa-"+(args.clustername)+"-calico.pem", "-days", "365", "-extensions", "v3_req", "-extfile", "openssl.cnf"], cwd='./tls')

        buffer_calicosa = open("./tls/sa-"+ str(args.clustername) +"-calico.pem", "rU").read()
        etcdsacalicobase64 = base64.b64encode(buffer_calicosa)
        buffercalicosa = open("./tls/sa-"+ str(args.clustername) +"-calico-key.pem", "rU").read()
        etcdsacalicokeybase64 = base64.b64encode(buffercalicosa)

        calicoconfig_template = (calico_template.render(
            etcdendpointsurls=iplist.rstrip(','),
            etcdcabase64=ETCDCAPEM,
            etcdsacalicobase64=etcdsacalicobase64,
            etcdsacalicokeybase64=etcdsacalicokeybase64
            ))

        with open('calico.yaml', 'w') as calico:
            calico.write(calicoconfig_template)

    def createClusterId():
        """Create and Retrieve ClusterID."""
        global etcdTokenId
        discoverurl = httplib.HTTPSConnection('discovery.etcd.io', timeout=10)
        discoversize = "/new?size="+ str(args.managers)
        discoverurl.request("GET", discoversize)
        #etcdTokenId = discoverurl.getresponse().read()
        etcdTokenId = discoverurl.getresponse().read()
        return etcdTokenId

    def printClusterInfo():
        """Print cluster info."""
        print("-"*40+"\n\nCluster Info:")
        print("Etcd ID token:\t" + str(etcdTokenId.rsplit('/', 1)[1]))
        print("k8s version:\t" + str(args.k8sver))
        print("Clustername:\t" + str(args.clustername))
        print("Cluster cidr:\t" + str(args.subnetcidr))
        print("Managers:\t" + str(args.managers))
        print("Workers:\t" + str(args.workers))
        print("Manager img:\t" +str(args.managerimageflavor))
        print("Worker img:\t" +str(args.workerimageflavor))
        print("VIP1:\t\t" + str(args.floatingip1))
        print("VIP2:\t\t" + str(args.floatingip2))
        print("Dnsserver:\t" +str(args.dnsserver))
        print("Net overlay:\t" + str(args.netoverlay))
        print("Auth mode:\t" + str(args.authmode))
        print("alphafeatures:\t" + str(args.alphafeatures))
        print("-"*40+"\n")
        print("To start building the cluster: \tterraform init && terraform plan && terraform apply && sh snat_acl.sh")
        print("To interact with the cluster: \tsh kubeconfig.sh")

        clusterstatusconfig_template = (clusterstatus_template.render(
            etcdendpointsurls=iplist.rstrip(','),
            etcdtoken=etcdTokenId,
            k8sver=args.k8sver,
            clustername=args.clustername,
            subnetcidr=args.subnetcidr,
            managers=args.managers,
            workers=args.workers,
            managerimageflavor=args.managerimageflavor,
            workerimageflavor=args.workerimageflavor,
            floatingip1=args.floatingip1,
            floatingip2=args.floatingip2,
            dnsserver=args.dnsserver,
            netoverlay=args.netoverlay,
            authmode=args.authmode,
            cloudprovider=args.cloudprovider,
            calicocidr=args.calicocidr,
            flannelver=args.flannelver,
            keypair=args.keypair
            ))

        with open('cluster.status', 'w') as k8sstat:
            k8sstat.write(clusterstatusconfig_template)

    if args.managers < 3:
        raise Exception('Managers need to be no less then 3.')

    iplist = ""
    for node in range(10, args.managers+10):
        apiserver = str("https://" + args.subnetcidr.rsplit('.', 1)[0] + "." + str(node) + ":2379,")
        iplist = iplist + apiserver

    initialclusterlist = ""
    for node in range(10, args.managers+10):
        apiserver = str("infra" + str(node-10) + "=https://" + args.subnetcidr.rsplit('.', 1)[0] + "." + str(node) + ":2380,")
        initialclusterlist = initialclusterlist + apiserver


    discovery_id = createClusterId()
    createCaCert()
    #create ServiceAccount certificate
    createSAcert()

    buffer = open('./tls/ca.pem', 'rU').read()
    CAPEM = base64.b64encode(buffer)

    buffer = open('./tls/etcd-ca.pem', 'rU').read()
    ETCDCAPEM = base64.b64encode(buffer)

    cloudconfig_template = (cloudconfig_template.render(
        authurl=os.environ["OS_AUTH_URL"],
        username=args.username,
        password=os.environ["OS_PASSWORD"],
        region=os.environ["OS_REGION_NAME"],
        projectname=args.projectname,
        tenantid=os.environ["OS_TENANT_ID"],
        ))

    with open('cloud.conf', 'w') as cloudconf:
        cloudconf.write(cloudconfig_template)


    buffer = open('cloud.conf', 'rU').read()
    cloudconfbase64 = base64.b64encode(buffer)

    k8stemplate = (template.render(
        username=args.username,
        projectname=args.projectname,
        clustername=args.clustername,
        managers=args.managers,
        workers=args.workers,
        subnetcidr=args.subnetcidr,
        calicocidr=args.calicocidr,
        keypair=args.keypair,
        workerimageflavor=args.workerimageflavor,
        managerimageflavor=args.managerimageflavor,
        floatingip1=args.floatingip1,
        floatingip2=args.floatingip2,
        ))



    for node in range(10, args.managers+10):
        lanip = str(args.subnetcidr.rsplit('.', 1)[0] + "." + str(node))
        nodeyaml = str("node_" + lanip.rstrip(' ') + ".yaml")
        createNodeCert(lanip, "manager")
        buffer = open("./tls/"+ str(lanip)+ "-k8s-node.pem", 'rU').read()
        k8snodebase64 = base64.b64encode(buffer)
        buffer = open('./tls/'+str(lanip)+"-k8s-node-key.pem", 'rU').read()
        k8snodekeybase64 = base64.b64encode(buffer)
        buffer = open('./tls/'+str(lanip)+"-etcd-node.pem", 'rU').read()
        etcdnodebase64 = base64.b64encode(buffer)
        buffer = open('./tls/'+str(lanip)+"-etcd-node-key.pem", 'rU').read()
        etcdnodekeybase64 = base64.b64encode(buffer)
        buffer = open("./tls/sa-"+str(args.clustername)+"-k8s.pem", 'rU').read()
        sak8sbase64 = base64.b64encode(buffer)
        buffer = open("./tls/sa-"+str(args.clustername)+"-k8s-key.pem", 'rU').read()
        sak8skeybase64 = base64.b64encode(buffer)


        manager_template = (cloudconf_template.render(
            managers=args.managers,
            workers=args.workers,
            dnsserver=args.dnsserver,
            etcdendpointsurls=iplist.rstrip(','),
            etcdid=(node-10),
            initialclusterlist=initialclusterlist.rstrip(','),
            floatingip1=args.floatingip1,
            k8sver=args.k8sver,
            flannelver=args.flannelver,
            netoverlay=args.netoverlay,
            cloudprovider=args.cloudprovider,
            authmode=args.authmode,
            clustername=args.clustername,
            subnetcidr=args.subnetcidr,
            calicocidr=args.calicocidr,
            ipaddress=lanip,
            ipaddressgw=(args.subnetcidr).rsplit('.', 1)[0]+".1",
            discoveryid=discovery_id,
            alphafeatures=args.alphafeatures,
            cabase64=CAPEM,
            etcdcabase64=ETCDCAPEM,
            k8snodebase64=k8snodebase64,
            k8snodekeybase64=k8snodekeybase64,
            etcdnodebase64=etcdnodebase64,
            etcdnodekeybase64=etcdnodekeybase64,
            cloudconfbase64=cloudconfbase64,
            sak8sbase64=sak8sbase64,
            sak8skeybase64=sak8skeybase64
            ))

        with open(nodeyaml, 'w') as controller:
            controller.write(manager_template)


    for node in range(10+args.managers, args.managers+args.workers+10):
        lanip = str(args.subnetcidr.rsplit('.', 1)[0] + "." + str(node))
        nodeyaml = str("node_" + lanip.rstrip(' ') + ".yaml")
        createNodeCert(lanip, "worker")
        buffer = open("./tls/"+ str(lanip)+ "-k8s-node.pem", 'rU').read()
        k8snodebase64 = base64.b64encode(buffer)
        buffer = open('./tls/'+str(lanip)+"-k8s-node-key.pem", 'rU').read()
        k8snodekeybase64 = base64.b64encode(buffer)
        buffer = open('./tls/'+str(lanip)+"-etcd-node.pem", 'rU').read()
        etcdnodebase64 = base64.b64encode(buffer)
        buffer = open('./tls/'+str(lanip)+"-etcd-node-key.pem", 'rU').read()
        etcdnodekeybase64 = base64.b64encode(buffer)

        worker_template = (cloudconf_template.render(
            isworker=1,
            managers=args.managers,
            workers=args.workers,
            dnsserver=args.dnsserver,
            etcdendpointsurls=iplist.rstrip(','),
            etcdid=(node-10),
            initialclusterlist=initialclusterlist.rstrip(','),
            floatingip1=args.floatingip1,
            k8sver=args.k8sver,
            flannelver=args.flannelver,
            netoverlay=args.netoverlay,
            cloudprovider=args.cloudprovider,
            authmode=args.authmode,
            clustername=args.clustername,
            subnetcidr=args.subnetcidr,
            calicocidr=args.calicocidr,
            ipaddress=lanip,
            ipaddressgw=(args.subnetcidr).rsplit('.', 1)[0]+".1",
            loadbalancer=(args.subnetcidr).rsplit('.', 1)[0]+".3",
            discoveryid=discovery_id,
            cabase64=CAPEM,
            etcdcabase64=ETCDCAPEM,
            k8snodebase64=k8snodebase64,
            k8snodekeybase64=k8snodekeybase64,
            etcdnodebase64=etcdnodebase64,
            etcdnodekeybase64=etcdnodekeybase64,
            cloudconfbase64=cloudconfbase64,
            ))

        with open(nodeyaml, 'w') as worker:
            worker.write(worker_template)

    createClientCert("admin")
    createCalicoObjects()

    kubeconfig_template = (kubeconfig_template.render(
        floatingip1=args.floatingip1,
        masterhostip=(args.subnetcidr).rsplit('.', 1)[0]+".10"
        ))


    with open('kubeconfig.sh', 'w') as kubeconfig:
        kubeconfig.write(kubeconfig_template)

    with open('k8s.tf', 'w') as k8s:
        k8s.write(k8stemplate)

except Exception as e:
    raise
else:
    printClusterInfo()
