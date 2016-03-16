import logging

import tempfile
import constants
import utils
import json


class AnsibleShell(object):
    """
    Runs remote commands for a single host by using ansible shell module.
    """
    def __init__(self, host, log=None):
        self.log = log or logging.getLogger('dummy')
        self.host = host

    def run(self, cmd, ansible_args=[]):
        """Creates temporary file with passed cmd (to guard the quoting e.g. for sed commands)
        and executes it on the remote host by using ansible 'script' module.

        Params
        ------
        cmd : str
            command to run
        ansible_args : list(str)
            other ansible command-line args

        Returns
        -------
        AnsibleShellTaskResult
        """
        result = None

        with tempfile.NamedTemporaryFile('w+') as script:
            self.log.info('Running ansible with command: {0}'.format(cmd))
            script.write(cmd)
            script.flush()

            result = utils.run_cmd([
                constants.ANSIBLE_BINARY,
                '-r',
                self.host,
                '-m', 'script',
                '-a', '{0}'.format(script.name),
            ] + ansible_args, self.log, debug_info=False)

        return AnsibleShellTaskResult(result.stdout)


class AnsibleShellTaskResult(object):
    """
    Parses results of an ansible shell task for a single host.
    """
    def __init__(self, ansible_output):
        """
        Params
        ------
        ansible_output: str
            for sample output run: ansible -r localhost -m shell -a 'echo foo'
        """
        self.ansible_result = json.loads(ansible_output)
        self.task_result = dict.popitem(self.ansible_result['plays'][0]['tasks'][0]['hosts'])[1]

    @property
    def failed(self):
        return 'rc' not in self.task_result or self.task_result['rc'] != 0

    @property
    def succeeded(self):
        return not self.failed

    @property
    def returncode(self):
        return self.task_result['rc']

    @property
    def stdout(self):
        return self.task_result['stdout']

    @property
    def stderr(self):
        return self.task_result['stderr']
