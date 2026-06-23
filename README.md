# Batch Command Executor TUI

A portable, single-file Python script that provides both a Terminal User Interface (TUI) and plain text mode for executing commands across multiple servers or objects. It supports sequential execution, parallel workers, dry-run preview, and real-time progress monitoring with status indicators.

## Preview

Here is a preview of the TUI when running a `ping` command over a list of servers:

```
+---------------------------------------------------------------------------------------+
| Objects                  | Live Output: server1.example.com                           |
|                          |                                                            |
| [▶] server1.example.com | PING server1.example.com (192.168.1.10): 56 data bytes     |
| [ ] server2.example.com  | 64 bytes from 192.168.1.10: icmp_seq=0 ttl=64 time=5.23 ms |
| [ ] bad-server.local     | 64 bytes from 192.168.1.10: icmp_seq=1 ttl=64 time=5.78 ms |
| [ ] another-host.com     | 64 bytes from 192.168.1.10: icmp_seq=2 ttl=64 time=4.99 ms |
| ...                      |                                                            |
|                          | --- server1.example.com ping statistics ---                |
|                          | 3 packets transmitted, 3 packets received, 0% packet loss  |
|                          |                                                            |
+---------------------------------------------------------------------------------------+
| All jobs completed. Press any key to exit.                                            |
+---------------------------------------------------------------------------------------+
```

**Status Indicators:**
- `[▶]` — Currently running
- `[✔]` — Success (green)
- `[✖]` — Failure (red)
- `[ ]` — Pending
- `[—]` — Skipped (due to failure prompt)

## Features

- **TUI Interface**: Clear, colorful terminal interface that shows object status and live command output side-by-side.
- **Real-time Status**: Each object is marked with its status: Pending `[ ]`, Running `[▶]`, Success `[✔]`, Fail `[✖]`, or Skipped `[—]`.
- **Scrollable Output Log**: The right pane is a continuous, terminal-like log of every object's combined stdout and stderr. Output is never overwritten — each object is appended under a `=== object ===` header. The pane auto-follows the newest output (like `tail -f`); scroll up to review older messages and it stops following until you return to the bottom.
- **Keyboard Controls**: Scroll the output pane any time — during a run, at a failure prompt, or in the end-of-run review — with **Up/Down** (line), **PgUp/PgDn** (page), **Home/End** (top/bottom; End resumes auto-follow).
- **End-of-Run Review**: When the run finishes, the summary is appended to the log and you stay in an interactive scroll view of the entire run. Press **q** to quit.
- **Interactive Failure Handling**: If a command fails, the script pauses and prompts you to **(s)kip** to the next object, **(a)utoskip** the rest (keep running, but don't prompt again on later failures), or **(e)xit** (which marks the remaining objects as skipped).
- **Scrolling Object List**: The list of objects automatically scrolls if it's longer than the terminal window, always keeping the active item in view.
- **Dry-Run Mode (`--dry-run`)**: Preview all commands that would be executed without actually running them — perfect for verifying your command before execution.
- **Parallel Execution (`-p N`)**: Run commands across multiple servers simultaneously using configurable worker threads for faster completion.
- **Plain Text Fallback (`--plain`)**: For terminals where curses doesn't work properly, use plain text output with timing information and summary.
- **Secure & Robust Command Parsing**: Uses Python's `shlex` module to parse commands. This prevents the local shell from expanding variables (like `$HOSTNAME`), allowing them to be correctly expanded on the remote machine. It also handles object names with spaces or special characters automatically.
- **Signal Handling**: Graceful cleanup on Ctrl+C or SIGTERM — terminates running subprocesses and restores terminal state.
- **Execution Summary**: Reports final counts of total, successful, failed, and skipped items. Failed objects are listed by name with their exit code and failure reason (e.g. `Connection refused`), both in the on-screen review and in a persistent report printed to your terminal scrollback after exit.
- **Portable**: A single Python script with no third-party dependencies — it runs on any Unix-like system (Linux, macOS, BSD) using only the standard library. Just copy it to a server and run. (Windows is not supported: the `curses` module isn't bundled with Windows Python, and the live-output streaming relies on `select()` over pipes, which is POSIX-only.)

## Requirements

- Python 3 (tested with 3.6+)
- A standard Linux, macOS, or other Unix-like terminal that supports `curses` (which is most of them)
- For TUI mode: minimum terminal size of **80 columns × 24 lines**
- If terminal is too small for TUI, use the `--plain` flag for text-only output

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
usage: executor.py [-h] -l LIST -c COMMAND [--dry-run] [--plain] [-p N]

A TUI wrapper to execute a command over a list of objects.

options:
  -h, --help            show this help message and exit
  -l LIST, --list LIST  Path to a text file containing a list of objects (one per line).
  -c COMMAND, --command COMMAND
                        The command to execute. Use '$object' as a placeholder for the object.
  --dry-run             Preview commands without executing them (safe mode).
  --plain               Use plain text output instead of TUI (for small terminals).
  -p N, --parallel N    Run commands in parallel with N workers (default: 1 for sequential).

Example Usage:
  ./executor.py --list servers.txt --command 'ssh $object "echo \$HOSTNAME ; df -h /"'

Options:
  --dry-run           Preview commands without executing them
  --plain             Use plain text output instead of TUI (for small terminals)
  -p N                Run commands in parallel with N workers (default: sequential)

Details:
  - The command string must contain the placeholder '$object'.
  - The script uses `shlex` to parse the command, which prevents the local
    shell from expanding variables like `$HOSTNAME`. Variables will be correctly
    expanded on the remote machine.
  - Object names with spaces or special characters are handled automatically.
  - Press 'q' during a command's execution to abort the entire script.

Dry Run Example:
  ./executor.py --list servers.txt --command 'ssh $object "uptime"' --dry-run
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

> **SSH Host Key Verification**: When connecting to new hosts, SSH may prompt or fail due to host key verification. Use `-o StrictHostKeyChecking=accept-new` to automatically accept unknown keys (recommended for automation), or `-o StrictHostKeyChecking=no` for testing (less secure).

### Dry-Run Mode (Safe Preview)

Preview all commands that would be executed without actually running them:

```bash
./executor.py --list servers.txt --command 'ssh $object "uptime"' --dry-run
```

Output example:
```
======================================================================
  DRY RUN MODE - No commands will be executed
======================================================================

Command: ssh $object "uptime"
Servers (5):

  1. ~ localhost
     ssh localhost "uptime"

  2. ~ 127.0.0.1
     ssh 127.0.0.1 "uptime"

  ...

======================================================================
  Total: 5 command(s) would be executed
======================================================================
```

### Plain Text Mode (Fallback for Small Terminals)

Use plain text output when curses doesn't work properly in your terminal:

```bash
./executor.py --list servers.txt --command 'echo "Testing $object" ; sleep 1' --plain
```

Output example:
```
======================================================================
  BATCH EXECUTOR - PLAIN TEXT MODE
======================================================================

[1/5] ▶ localhost
       Command: echo "Testing localhost" ; sleep 1
       Testing localhost
       Status: ✔ (completed in 1.00s)

...

======================================================================
  SUMMARY: 5 total | 5 succeeded | 0 failed
======================================================================
```

### Parallel Execution Mode

Run commands across multiple servers simultaneously using worker threads:

```bash
# Execute with 4 parallel workers
./executor.py --list servers.txt --command 'ssh $object "uptime"' -p 4
```

Output example:
```
======================================================================
  PARALLEL MODE - 4 workers
======================================================================

Running commands in parallel...

  [1/5] ✔ localhost
  [2/5] ✖ bad-server.local
  [3/5] ✔ 127.0.0.1
  ...
```

> **Note**: Parallel mode doesn't show live output per command due to concurrent execution. Use sequential mode or `--plain` for detailed output tracking.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
