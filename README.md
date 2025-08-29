# Batch Command Executor TUI

A simple, portable, single-file Python script that provides a Terminal User Interface (TUI) for executing a command over a list of objects (like servers, users, etc.).

It allows you to monitor the real-time output of your commands and see the status of each job in a clean, colorful interface.

## Preview

Here is a preview of the TUI when running a `ping` command over a list of servers:

```
+-----------------------------------------------------------------------------+
| Objects               | Live Output: server1.example.com                    |
|                       |                                                     |
| [▶] server1.example.com | PING server1.example.com (192.168.1.10): 56 data bytes |
| [ ] server2.example.com | 64 bytes from 192.168.1.10: icmp_seq=0 ttl=64 time=5.23 ms |
| [ ] bad-server.local  | 64 bytes from 192.168.1.10: icmp_seq=1 ttl=64 time=5.78 ms |
| [ ] another-host.com  | 64 bytes from 192.168.1.10: icmp_seq=2 ttl=64 time=4.99 ms |
| ...                   |                                                     |
|                       | --- server1.example.com ping statistics ---         |
|                       | 3 packets transmitted, 3 packets received, 0% packet loss |
|                       |                                                     |
+-----------------------------------------------------------------------------+
| All jobs completed. Press any key to exit.                                  |
+-----------------------------------------------------------------------------+
```

## Features

- **Rich TUI Interface**: Clear, colorful terminal interface that shows object status and live command output side-by-side.
- **Real-time Status**: Each object is marked with its status: Pending `[ ]`, Success `[✔]`, or Fail `[✖]`.
- **Live Command Output**: The output of the currently running command is streamed to the UI in real-time.
- **Interactive Failure Handling**: If a command fails, the script pauses and prompts you to either **(s)kip** to the next object or **(e)xit**.
- **Scrolling Lists**: The list of objects automatically scrolls if it's longer than the terminal window, always keeping the active item in view.
- **Secure & Robust Command Parsing**: Uses Python's `shlex` module to parse commands. This prevents the local shell from expanding variables (like `$HOSTNAME`), allowing them to be correctly expanded on the remote machine. It also handles object names with spaces or special characters automatically.
- **Portable**: It's a single Python script with no external dependencies. Just copy it to a server and run.

## Requirements

- Python 3
- A standard Linux, macOS, or other Unix-like terminal that supports `curses` (which is most of them).

## Installation

1.  Download the `executor.py` script to your machine.
2.  Make it executable:
    ```bash
    chmod +x executor.py
    ```

That's it! There are no packages to install.

## Usage

The script takes two required arguments: `--list` (or `-l`) and `--command` (or `-c`).

```
./executor.py --help
```

```
usage: executor.py [-h] -l LIST -c COMMAND

A TUI wrapper to execute a command over a list of objects.

options:
  -h, --help            show this help message and exit
  -l LIST, --list LIST  Path to a text file containing a list of objects (one per line).
  -c COMMAND, --command COMMAND
                        The command to execute. Use '$object' as a placeholder for the object.

Example Usage:
  ./executor.py --list servers.txt --command 'ssh $object "echo \$HOSTNAME ; df -h /"'

Details:
  - The command string must contain the placeholder '$object'.
  - The script uses `shlex` to parse the command, which prevents the local
    shell from expanding variables like `$HOSTNAME`. Variables will be correctly
    expanded on the remote machine.
  - Object names with spaces or special characters are handled automatically.
  - Press 'q' during a command's execution to abort the entire script.
```

### The Object File

The file passed to `--list` should be a simple text file with one object per line, like this:

**servers.txt**
```
server1.example.com
server2.example.com
server-with-a-space
192.168.1.50
```

### Examples

1.  **Simple Test (Safe to Run)**

    A great way to test the interface without running any real commands.

    ```bash
    ./executor.py --list servers.txt --command 'echo "Pinging $object..." && sleep 2'
    ```

2.  **Ping a List of Servers**

    ```bash
    ./executor.py --list servers.txt --command 'ping -c 3 $object'
    ```

3.  **Run Remote Commands via SSH**

    This example runs two commands on each remote server. Note that `$HOSTNAME` is correctly expanded by the *remote* server's shell, not your local one, thanks to `shlex`.

    ```bash
    ./executor.py --list servers.txt --command 'ssh $object "echo $HOSTNAME ; df -h /"'
    ```

4.  **Check for a File on Remote Servers**

    ```bash
    ./executor.py --list servers.txt --command 'ssh $object "ls /var/log/app.log"'
    ```

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
