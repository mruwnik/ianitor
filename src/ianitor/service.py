# -*- coding: utf-8 -*-
# Copyright (C) 2014 by Clearcode <http://clearcode.cc>
# and associates (see AUTHORS.md).

# This file is part of ianitor.

# ianitor is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# ianitor is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.

# You should have received a copy of the GNU Lesser General Public License
# along with ianitor.  If not, see <http://www.gnu.org/licenses/>.

from contextlib import contextmanager
from collections import defaultdict
import subprocess
import logging
from requests import ConnectionError

logger = logging.getLogger(__name__)


@contextmanager
def ignore_connection_errors(action="unknown"):
    try:
        yield
    except ConnectionError:
        logger.error("connection error on <%s> action failed" % action)


class Service(object):
    EXECUTABLE_CHECKS = 'exec'
    CALLABLE_CHECKS = 'call'

    def __init__(self, command, session, ttl, service_name,
                 service_id=None, tags=None, port=None,
                 checks=None, interval=1):
        self.command = command
        self.session = session
        self.process = None

        self.ttl = ttl
        self.service_name = service_name
        self.tags = tags or []
        self.port = port
        self.service_id = service_id or service_name

        self.check_id = "service:" + self.service_id
        self.checks = checks or defaultdict(list)
        self.interval = interval

    def start(self):
        """ Start service process.

        :return:
        """
        logger.debug("starting service: %s" % " ".join(self.command))
        self.process = subprocess.Popen(self.command)
        self.register()

    def is_up(self):
        """
        Poll service process to check if service is up.

        :return:
        """
        logger.debug("polling service")
        return bool(self.process) and self.process.poll() is None

    def kill(self):
        """
        Kill service process and make sure it is deregistered from consul
        cluster.

        :return:
        """
        logger.debug("killing service")
        if self.process is None:
            raise RuntimeError("Process does not exist")

        self.process.kill()
        self.deregister()

    def register(self):
        """
        Register the service and all associated checks in the consul cluster.

        :return: None
        """
        logger.debug("registering service")
        with ignore_connection_errors():
            self.session.agent.service.register(
                name=self.service_name,
                service_id=self.service_id,
                port=self.port,
                tags=self.tags,
                # format it into XXXs format
                ttl="%ss" % self.ttl,
            )
        self.register_checks()

    def register_checks(self):
        """Register extra checks with consul."""
        logger.debug("registering extra checks")
        for i, check in enumerate(self.checks[self.EXECUTABLE_CHECKS]):
            with ignore_connection_errors():
                self.session.agent.check.register(
                    service_id=self.service_id,
                    check_id=self._check_id(i),
                    name="{service} check '{check}'".format(service=self.service_name, check=check),
                    script=check,
                    # format it into XXXs format
                    interval="%ss" % self.interval,
                )

    def deregister(self):
        """
        Deregister the service and all associated checks from the consul cluster.

        :return: None
        """
        self.deregister_checks()
        logger.debug("deregistering service")

        with ignore_connection_errors("deregister"):
            self.session.agent.service.deregister(self.service_id)

    def deregister_checks(self):
        """Deregister extra checks with consul."""
        logger.debug("deregistering extra checks")

        for i, check in enumerate(self.checks[self.EXECUTABLE_CHECKS]):
            with ignore_connection_errors("deregistering check '%s'" % check):
                self.session.agent.check.deregister(check_id=self._check_id(i))

    def keep_alive(self):
        """
        Keep alive service in consul cluster marking TTL check pass
        on consul agent.

        If some cases it can happen that service registry disappeared from
        consul cluster. This method registers service again if it happens.

        :return: None
        """
        # check whether all callables are OK
        for check in self.checks[self.CALLABLE_CHECKS]:
            # TODO: call the check, and on the basis of what it gets back, update the service's status
            pass

        with ignore_connection_errors("ttl_pass"):
            if not self.session.health.check.ttl_pass(self.check_id):
                # register and ttl_pass again if it failed
                logger.warning("service keep-alive failed, re-registering")
                self.register()
                self.session.health.check.ttl_pass(self.check_id)

    def __del__(self):
        """
        Cleanup processes on del
        """
        if self.process and self.process.poll() is None:
            self.kill()

    def _check_id(self, check_no):
        """
        Get the check id of the given check.

        :return: the check id
        """
        return "{service}_check_{check_no}".format(service=self.service_name, check_no=check_no)
