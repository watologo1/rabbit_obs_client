#!/usr/bin/python3

# Author: Thomas Renninger <trenn@suse.de>
# Copyright (c) 2020 SUSE LLC
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.


import argparse
import configparser
import json
import logging
import os
import pwd
from subprocess import Popen, run, TimeoutExpired
import sys
from datetime import datetime

import pika

# https://openbuildservice.org/help/manuals/obs-admin-guide/obs.cha.administration.html#_rabbit_mq_configuration
# https://rabbit.opensuse.org

LOG_DIR = "/var/log/rabbit_obs/"
DOWNLOAD_DIR = "/run/rabbit_obs_client"
INST_DIR = "/usr/share/rabbit_obs_client"
DFLT_TRIGGER = "/bin/true"


class OBSConnect(object):

    def __init__(self, bs="obs"):
        self.pkg_list = []
        self.pkg_dflts = {'project': '', 'package': '', 'repo': '',
                          'user': '', 'buildarch': '', 'pkg_arch': '', 'trigger_cmd': DFLT_TRIGGER}
        self.uids = {}
        self.gids = {}

        if bs == "obs":
            self.rabbiturl = "amqps://opensuse:opensuse@rabbit.opensuse.org"
            self.api = "https://api.opensuse.org"
            self.rpm_url = "https://build.opensuse.org/package/binary/download"
            self.amqp_prefix = "opensuse.obs"
            self.conf_file = "/etc/rabbit_obs.conf"
        elif bs == "ibs":
            # ToDo: This is untested
            self.rabbiturl = "amqps://suse:suse@rabbit.suse.de"
            self.api = "https://api.suse.de"
            self.rpm_url = "https://build.suse.de/package/binary/download"
            self.amqp_prefix = "suse.ibs"
            self.conf_file = "/etc/rabbit_ibs.conf"
        else:
            raise Exception("Fatal %s server type does not exist" % bs)

        self.listen_key = self.amqp_prefix + ".package.build_success"
        self._read_config(self.conf_file)

    def _read_config(self, file: str) -> None:
        output = ""
        config = configparser.ConfigParser()
        config.read(file)
        for key in config.sections():
            new_pkg = {}
            for conf in self.pkg_dflts.keys():
                new_pkg[conf] = (config[key].get(conf) or self.pkg_dflts[conf])
                if not new_pkg[conf]:
                    raise Exception("Package monitor section [%s] does not specify: [%s]" % (key, conf))
                if conf == "user":
                    t_pwd = pwd.getpwnam(new_pkg[conf])
                    self.uids[new_pkg[conf]] = t_pwd.pw_uid
                    self.gids[new_pkg[conf]] = t_pwd.pw_gid
            self.pkg_list.append(new_pkg)
            output += '%s\t%s\t%s\n' % (new_pkg['package'], new_pkg['project'], new_pkg['repo'])
        logging.info("Found %s package(s) to listen for:\n%s" % (len(self.pkg_list), output))
        logging.info(self.pkg_list)

    def get_rpm(self, pkg_hit: dict):
        """ We received an event with a package hit.
            Now retrieve the built rpm from the server"""

        t_dir = DOWNLOAD_DIR + '/' + pkg_hit['project'] + \
                    '_' + pkg_hit['package'] + '_' + pkg_hit['repo'] + '_' + \
                    str(datetime.utcnow().strftime('%d_%m_%Y_%H_%M_%S'))
        osc_cmd = 'sudo -u {osc_user} /usr/bin/osc -A {osc_api}  getbinaries -d {download_dir} {project} {package} {repo} {buildarch}'.format(
            osc_user=pkg_hit["user"],
            osc_api=self.api,
            download_dir=t_dir,
            project=pkg_hit["project"],
            package=pkg_hit["package"],
            repo=pkg_hit["repo"],
            buildarch=pkg_hit["buildarch"])

        try:
            logging.info("Making dir: [%s]" % t_dir)
            if not os.path.isdir(t_dir):
                os.makedirs(t_dir)
            if not os.path.isdir(t_dir):
                logging.error("Failed to make dir: %s", t_dir)
                return
            logging.info("Calling script: %s", osc_cmd)
            os.chown(t_dir, self.uids[pkg_hit["user"]], self.gids[pkg_hit["user"]])
            run(osc_cmd.split(), timeout=20, check=True)
        #    except subprocess.CalledProcessError as E:
        #        logging.error("Calling script: %s failed\n%s", OSC_CMD, E.output)
        except TimeoutExpired as E:
            logging.error("Calling script timed out: %s failed\n%s", osc_cmd, E.output)
            return None
        except Exception as E:
            logging.error("Calling download script script failed\n:%s\n%s", osc_cmd, E)
            return None
        return t_dir

    def update_pkg(self, t_dir):

#        exec_cmd = ['zypper', 'update', '--no-confirm', '--allow-vendor-change', '-r', \
#                'home_trenn_cobbler_test_build cobbler']
        exec_cmd = ['rpm', '-Uvh', t_dir + '/*.rpm']
        logging.info("Calling script: %s", " ".join(exec_cmd))
        try:
            run(exec_cmd, timeout=20, check=True)
        except Exception as E:
            logging.error("Calling download script script failed\n:%s\n%s", exec_cmd, E)
            return None

    def trigger_cmd(self, pkg):

        log_file = LOG_DIR + pkg['project'] + \
                    '_' + pkg['package'] + '_' + pkg['repo'] + '_' + \
                    str(datetime.utcnow().strftime('%Y-%d-%m_%H-%M-%S')) + '.log'
        try:
            with open(log_file, "wb") as out:
                exec_cmd = [pkg['trigger_cmd'], pkg['project'], pkg['package'], pkg['repo']]
                logging.info("Calling script: %s", " ".join(exec_cmd))
                logging.info("Logging script to : %s", log_file)
                Popen(exec_cmd, shell=True,
                      stdout=out, stderr=out, bufsize=-1)
        except Exception as E:
            logging.error("Error when executing script [%s]: %s" % (exec_cmd, E))
            return False
        return True

    def process_package_match(self, pkg_hit):

        t_dir = self.get_rpm(pkg_hit)
        if not t_dir:
            logging.error("Could not download rpm(s)")
            return

        if self.update_pkg(t_dir) is None:
            return

        if self.trigger_cmd(pkg_hit) is False:
            return

    def rabbit_cb(self, ch, method, properties, body):

        if not method or not method.routing_key:
            return

        logging.debug(" [x] %r:%r" % (method.routing_key, body))

        try:
            decoded_body = json.loads(body.decode('utf-8'))
            project = decoded_body.get("project")
            package = decoded_body.get("package")
            arch = decoded_body.get("arch")
            repo = decoded_body.get("repository")
            #            versrel = decoded_body.get("versrel")
            #            reason = decoded_body.get("reason")
            #            bcnt = decoded_body.get("bcnt")
        except ValueError as e:
            logging.error(e)
            logging.error(" [x] %r:%r" % (method.routing_key, body))
            return

        if not project or not package or not arch or not repo:
            logging.warning("Suspicious build success even missing info %s" % body)
            return

        pkg_hit = None
        for i, d in enumerate(self.pkg_list):
            if d["project"] == project and \
                    d["package"] == package and \
                    d["repo"] == repo and \
                    d["buildarch"] == arch:
                pkg_hit = d
                break

        if not pkg_hit:
            return

        logging.info(" [x] %r:%r" % (method.routing_key, body))
        self.process_package_match(pkg_hit)

    def connect(self):
        # connection = pika.BlockingConnection(pika.URLParameters("amqps://suse:suse@rabbit.suse.de"))
        # See https://pypi.org/project/pika/
        # Threading multiple connections (obs and ibs) is hard
        # Currently only one connecion is supported
        while True:
            try:
                connection = pika.BlockingConnection(pika.URLParameters(
                    "amqps://opensuse:opensuse@rabbit.opensuse.org"))
                channel = connection.channel()

                channel.exchange_declare(exchange='pubsub', exchange_type='topic',
                                         passive=True, durable=True)

                result = channel.queue_declare("", exclusive=True)
                queue_name = result.method.queue

                channel.queue_bind(exchange='pubsub', queue=queue_name,
                                   routing_key=self.listen_key)

                print(' [*] Waiting for logs. To exit press CTRL+C')

                channel.basic_consume(queue_name,
                                      self.rabbit_cb,
                                      auto_ack=True)

                channel.start_consuming()
            except pika.exceptions.ConnectionClosedByBroker:
                break
            # Don't recover on channel errors
            except pika.exceptions.AMQPChannelError:
                break
            # Recover on all other connection errors
            except pika.exceptions.AMQPConnectionError:
                continue


def main():

    # Logging
    logging.basicConfig(filename=LOG_DIR + 'rabbit.log',
                        format='%(asctime)s %(message)s',
                        level=logging.INFO)
    logging.getLogger().addHandler(logging.StreamHandler())
    logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))

    # Argument parsing
    parser = argparse.ArgumentParser()
    parser.parse_args()
    parser.add_argument("--server", help="OpenSUSE Build Server", choices=["obs", "ibs"], default="obs", required=False)
    args = parser.parse_args()

    try:
        if not os.path.isdir(DOWNLOAD_DIR):
            os.makedirs(DOWNLOAD_DIR)
    except Exception:
        logging.error("Could not create download directory: %s" % DOWNLOAD_DIR)
        exit(1)

    # Start server
    server = OBSConnect(args.server)
    server.connect()
    # Testing without connect to rabbitmq event server
    # pkg = {'project': 'home:trenn:cobbler_test_build', 'package': 'cobbler',
    #       'repo': 'SLE_15_SP1', 'user': 'trenn', 'buildarch': 'x86_64', 'pkg_arch': 'noarch',
    #       'trigger_cmd': 'echo hallo'}
    # server.process_package_match(pkg)


if __name__ == '__main__':
    main()
