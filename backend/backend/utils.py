import subprocess
import logging
import munch

def run_cmd(cmd, log=logging.getLogger('dummy'), debug_info=False):
    """Runs given command in a subprocess.

    Params
    ------
    cmd : list(str)
        command to be executed and its arguments

    Returns
    -------
    munch.Munch(stdout, stderr, returncode)
        standard output, error output, and the return code
    """
    if debug_info:
        log.info("Executing: "+" ".join(cmd))

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (stdout, stderr) = process.communicate()

    if debug_info:
        log.info(stdout)

    if process.returncode or stderr:
        log.error("Error running command: {0}".format(" ".join(cmd)))
        log.error("return code: {0}".format(process.returncode))
        log.error("stderr: {0}".format(stderr))

    return munch.Munch(
        stdout = stdout,
        stderr = stderr,
        returncode = process.returncode
    )
