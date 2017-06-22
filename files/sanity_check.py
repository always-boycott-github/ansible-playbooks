#!/usr/bin/env python

import collections
import json
import os
import socket
import subprocess
import sys
import time
import urllib2


SanityCheckResult = collections.namedtuple(
    'SanityCheckResult', ['is_ok', 'message']
)


Mount = collections.namedtuple("Mount", ['mount', 'device'])


DEFAULT_PORT_RETRIES = 2
DEFAULT_PORT_RETRY_DELAY = 5
DEFAULT_HTTP_RETRIES = 3
DEFAULT_HTTP_RETRY_DELAY = 1


def is_port_open(host, port):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(.1)
        s.connect((host, port))
        s.shutdown(socket.SHUT_RDWR)
        return True
    except socket.error:
        return False


def check_port(host, port, retries=DEFAULT_PORT_RETRIES, retry_delay=DEFAULT_PORT_RETRY_DELAY):
    if is_port_open(host, port):
        return True
    retry_number = 1
    while retry_number <= retries:
        time.sleep(retry_delay)
        if is_port_open(host, port):
            return True
        retry_number += 1
    return False


def check_command(command):
    try:
        devnull = open(os.devnull, 'w')
        subprocess.check_call(
            command, shell=True, stdout=devnull, stderr=subprocess.STDOUT
        )
        return True
    except subprocess.CalledProcessError:
        return False


def get_free_percentage_on_mount(mount):
    stats = os.statvfs(mount)
    return stats.f_bavail / float(stats.f_blocks) * 100


def test_free_space_on_mount(mount, free_percentage_required):
    free_percentage = get_free_percentage_on_mount(mount)
    if free_percentage_required > free_percentage:
        message = "Not enough free space on {mount}. Free space is {percentage:.1f}%".format(
            mount=mount,
            percentage=free_percentage
        )
        return SanityCheckResult(False, message)
    message = "Enough free space {mount}. Free space is {percentage:.1f}%".format(
        mount=mount,
        percentage=free_percentage
    )
    return SanityCheckResult(True, message)


def send_message(recipient, subject, body):
    """
    While sending mail is not reliable, mail delivery is async, and mail always
    exits successfully.
    """
    process = subprocess.Popen(['mail', '-s', subject, recipient], stdin=subprocess.PIPE)
    process.communicate(body)


def get_dev_mounts():
    """
    :return: List of mounted drives, we assume that drives will have
             device column starting with "/dev".
    """

    with open("/proc/mounts") as f:
        proc_mounts = f.read().strip()

    results = []

    for line in proc_mounts.split("\n"):
        parts = line.split()
        mount = Mount(parts[1], parts[0])
        if mount.device.startswith("/dev"):
            results.append(mount)
    return results


def test_global_free_percentage(default_free_space_percentage):
    for mount in get_dev_mounts():
        yield test_free_space_on_mount(mount.mount, default_free_space_percentage)


def test_ports(ports):
    response = []
    for port_config in ports:
        host, port = port_config['host'], int(port_config['port'])
        message = port_config['message']
        retries = int(port_config.get('retries', DEFAULT_PORT_RETRIES))
        retry_delay = float(port_config.get('retry_delay', DEFAULT_PORT_RETRY_DELAY))
        if not check_port(host, port, retries=retries, retry_delay=retry_delay):
            response.append(SanityCheckResult(False, message))
        else:
            message = "Port {}:{} is getting connections".format(host, port)
            response.append(SanityCheckResult(True, message))
    return response


def test_commands(commands):
    response = []
    for command in commands:
        if not check_command(command['command']):
            response.append(SanityCheckResult(False, command['message']))
        else:
            message = format("Command {} returned exit status 0".format(
                command['command']
            ))
            response.append(SanityCheckResult(True, message))
    return response


def report_is_ok(report):
    return all(check.is_ok for check in report)


def format_report(report):
    lines = []
    report = sorted(report, key= lambda check: check.is_ok)
    for check in report:
        status = "OK" if check.is_ok else "ERROR"
        lines.append("#. {status} {message}".format(status=status, message=check.message))
    return "\n".join(lines)


def sanity_check(json_data):
    report = []
    report.extend(
        test_global_free_percentage(float(json_data['free_percentage']))
    )
    report.extend(
        test_ports(json_data['ports'])
    )
    report.extend(
        test_commands(json_data['commands'])
    )

    if report_is_ok(report):
        if 'snitch' in json_data and json_data['snitch']:
            if not ping_http_endpoint(json_data['snitch']):
                report.append(SanityCheckResult(False, "Couldn't send snitch"))

    if not report_is_ok(report):
        report_text = format_report(report)
        send_message(json_data['send_report_to'], json_data['subject'], report_text)

    with open(json_data['report_file'], 'w') as f:
        report_text = format_report(report)
        f.write(report_text)


def ping_http_endpoint(url, max_retries=DEFAULT_HTTP_RETRIES, delay=DEFAULT_HTTP_RETRY_DELAY):
    for dummy in range(max_retries):
        try:
            response = urllib2.urlopen(url, timeout=5)
        except (urllib2.URLError, urllib2.HTTPError) as e:
            pass
        else:
            if 200 <= response.getcode() < 300:
                return True
        time.sleep(delay)
    return False

if __name__ == "__main__":
    data_file = sys.argv[1]
    with open(data_file, 'r') as f:
        json_data = json.load(f)
    sanity_check(json_data)
