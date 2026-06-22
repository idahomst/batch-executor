#!/usr/bin/env python3
"""
Batch Executor TUI - Execute commands across multiple servers with real-time monitoring.

A terminal user interface for executing a command over a list of objects (servers, hosts, etc.)
sequentially or in parallel, displaying live output and status indicators.

Features:
  - Real-time TUI with color-coded status indicators
  - Live command output streaming
  - Interactive failure handling (skip/abort)
  - Dry-run mode to preview commands without executing
  - Optional parallel execution
  - Signal-safe cleanup on interrupt
"""
import curses
import subprocess
import argparse
import sys
import os
import shlex
import signal
import shutil
import re
import select
from time import sleep

# ANSI escape sequence pattern (colors, cursor movement, clear screen, etc.)
ANSI_ESCAPE_PATTERN = re.compile(r'\x1b\[[0-9;?]*[mGKJH]|\x1b\[?[0-9;?]*[A-Za-z]|\r')


def strip_ansi(text):
    """Remove ANSI escape sequences from text."""
    if not isinstance(text, str):
        return text
    return ANSI_ESCAPE_PATTERN.sub('', text)


def failure_reason(output_lines):
    """Return the last non-empty output line as a human-readable failure reason.

    For SSH failures (stderr is merged into stdout), this surfaces messages like
    'Connection refused' or 'Permission denied (publickey)'.
    """
    for line in reversed(output_lines):
        cleaned = strip_ansi(line).strip()
        if cleaned:
            return cleaned
    return "(no output)"

# --- Configuration ---
PLACEHOLDER = "$object"
STATUS_PENDING = "[ ]"
STATUS_RUNNING = "[▶]"
STATUS_SUCCESS = "[✔]"
STATUS_FAIL = "[✖]"
STATUS_SKIPPED = "[—]"
STATUS_DRYRUN = "~"  # Dry-run marker

# Minimum terminal dimensions
MIN_WIDTH = 80
MIN_HEIGHT = 24

# Track nodelay state (curses windows don't have getdelay())
_nodelay_active = False


class AbortSignal(Exception):
    """Raised when user requests abort during execution."""
    pass


class SkipException(Exception):
    """Raised when user requests to skip current item on failure."""
    pass


# --- Terminal Helpers ---

def check_terminal_size():
    """Validate that terminal meets minimum size requirements. Returns (height, width)."""
    height = shutil.get_terminal_size().lines
    width = shutil.get_terminal_size().columns
    
    if height < MIN_HEIGHT:
        print(f"Error: Terminal too small for TUI mode. "
              f"Minimum height is {MIN_HEIGHT} lines (got {height}).", file=sys.stderr)
        print("Try running with --plain for text-only output.", file=sys.stderr)
        sys.exit(1)
    if width < MIN_WIDTH:
        print(f"Error: Terminal too narrow for TUI mode. "
              f"Minimum width is {MIN_WIDTH} columns (got {width}).", file=sys.stderr)
        print("Try running with --plain for text-only output.", file=sys.stderr)
        sys.exit(1)
    return height, width


def signal_handler(signum, frame):
    """Handle SIGINT/SIGTERM for clean shutdown."""
    sig_name = signal.Signals(signum).name
    print(f"\n\nReceived {sig_name}. Cleaning up...")
    # Clean exit - subprocesses will be terminated by their parent on process exit
    sys.exit(130 if signum == signal.SIGINT else 143)


# --- TUI Drawing Functions ---

def draw_title(win, title):
    """Draws a title bar on a window."""
    win.erase()
    h, w = win.getmaxyx()
    try:
        win.border(0)
        display_title = f" {title} "[:w-2]
        win.addstr(0, 1, display_title.ljust(w-2), curses.A_BOLD)
    except curses.error:
        pass
    win.refresh()


def draw_list(win, objects, statuses, current_index, scroll_offset):
    """Draws the list of objects and their statuses, handling scrolling."""
    try:
        draw_title(win, "Objects")
    except curses.error:
        pass
    
    h, w = win.getmaxyx()
    visible_lines = max(h - 2, 1)

    for i in range(visible_lines):
        obj_index = scroll_offset + i
        if obj_index >= len(objects):
            break

        try:
            status_symbol = statuses[obj_index]

            # Determine color and style
            color = curses.color_pair(0)  # Default (white/black)
            if status_symbol == STATUS_SUCCESS:
                color = curses.color_pair(1)  # Green
            elif status_symbol == STATUS_FAIL:
                color = curses.color_pair(2)  # Red
            elif status_symbol == STATUS_SKIPPED:
                color = curses.color_pair(4)  # Yellow
            elif status_symbol == STATUS_DRYRUN:
                color = curses.color_pair(5)  # Cyan

            style = curses.A_NORMAL
            if obj_index == current_index:
                style = curses.A_REVERSE
                status_symbol = STATUS_RUNNING if statuses[obj_index] != STATUS_DRYRUN else STATUS_DRYRUN

            # Truncate object name if it's too long
            max_len = w - len(status_symbol) - 4
            obj = objects[obj_index]
            display_obj = (obj[:max_len] + '..') if len(obj) > max_len else obj

            win.addstr(i + 1, 2, f"{status_symbol} {display_obj}", color | style)
        except curses.error:
            pass
    
    win.refresh()


class OutputConsole:
    """A scrollable, terminal-like output pane.

    Holds every line of output produced during the run (across all objects) in
    a single append-only buffer. The visible region is a window into that
    buffer controlled by `view_top`. While `follow` is set, the view stays
    pinned to the newest output (like `tail -f`); scrolling up turns follow off,
    and scrolling back to the bottom (or pressing End) turns it back on.
    """

    # Keys that scroll the pane.
    SCROLL_KEYS = frozenset({
        curses.KEY_UP, curses.KEY_DOWN, curses.KEY_PPAGE, curses.KEY_NPAGE,
        curses.KEY_HOME, curses.KEY_END,
    })

    def __init__(self, stdscr, win):
        self.stdscr = stdscr
        self.win = win
        self.lines = []
        self.view_top = 0
        self.follow = True

    def _height(self):
        return max(self.win.getmaxyx()[0], 1)

    def _max_top(self):
        return max(0, len(self.lines) - self._height())

    def append(self, *texts):
        """Append one or more lines (ANSI stripped) and refresh if following."""
        for text in texts:
            for piece in strip_ansi(text).split('\n'):
                self.lines.append(piece)
        if self.follow:
            self.view_top = self._max_top()
            self.render()

    def render(self):
        win = self.win
        h, w = win.getmaxyx()
        try:
            win.erase()
            for row in range(h):
                idx = self.view_top + row
                if idx >= len(self.lines):
                    break
                line = self.lines[idx]
                try:
                    win.addstr(row, 0, line[:max(w - 1, 0)])
                except curses.error:
                    pass
            win.refresh()
        except curses.error:
            pass

    def handle_key(self, key):
        """Apply a scroll key. Returns True if the key was a scroll key."""
        if key not in self.SCROLL_KEYS:
            return False
        page = max(self._height() - 1, 1)
        if key == curses.KEY_UP:
            self.view_top -= 1
        elif key == curses.KEY_DOWN:
            self.view_top += 1
        elif key == curses.KEY_PPAGE:
            self.view_top -= page
        elif key == curses.KEY_NPAGE:
            self.view_top += page
        elif key == curses.KEY_HOME:
            self.view_top = 0
        elif key == curses.KEY_END:
            self.view_top = self._max_top()
        # Clamp and recompute follow (on when pinned to the bottom).
        self.view_top = max(0, min(self.view_top, self._max_top()))
        self.follow = self.view_top >= self._max_top()
        self.render()
        return True

    def pump_input(self):
        """Drain any pending scroll keys without blocking (nodelay mode)."""
        while True:
            try:
                key = self.stdscr.getch()
            except curses.error:
                break
            if key == -1:
                break
            self.handle_key(key)

    def review(self):
        """Block in an interactive scroll loop until the user presses q."""
        global _nodelay_active
        was_nodelay = _nodelay_active
        if was_nodelay:
            self.stdscr.nodelay(False)
            _nodelay_active = False
        self.render()
        try:
            while True:
                key = self.stdscr.getch()
                if key in (ord('q'), ord('Q')):
                    break
                self.handle_key(key)
        finally:
            if was_nodelay:
                self.stdscr.nodelay(True)
                _nodelay_active = True


def show_prompt(stdscr, message, console=None):
    """Display a blocking prompt at the bottom of the screen.

    While waiting, scroll keys are passed through to `console` (if given) so the
    output pane can still be scrolled; the prompt is redrawn on top after each
    scroll. Returns the key the user pressed.
    """
    global _nodelay_active
    was_nodelay = _nodelay_active
    if was_nodelay:
        stdscr.nodelay(False)
        _nodelay_active = False

    h, w = stdscr.getmaxyx()
    prompt_win = curses.newwin(3, min(w, 60), max(h - 4, 0), max((w - 60) // 2, 0))

    def draw_prompt():
        prompt_win.border()
        prompt_win.addstr(1, 2, message[:58].ljust(58), curses.A_BOLD | curses.color_pair(3))
        prompt_win.refresh()

    try:
        draw_prompt()
        while True:
            choice = stdscr.getch()
            if choice in (ord('s'), ord('S'), ord('a'), ord('A'), ord('e'), ord('E')):
                break
            if console is not None and console.handle_key(choice):
                draw_prompt()  # redraw prompt above the freshly scrolled output
    finally:
        try:
            prompt_win.hide()
            del prompt_win
        except Exception:
            pass
        if was_nodelay:
            stdscr.nodelay(True)
            _nodelay_active = True

    return choice


def show_summary(stdscr, statuses, objects=None):
    """Show final summary screen with pass/fail counts."""
    h, w = stdscr.getmaxyx()
    
    # Temporarily switch to blocking mode (nodelay isn't queryable)
    global _nodelay_active
    if _nodelay_active:
        stdscr.nodelay(False)
    
    try:
        summary_win = curses.newwin(h, w, 0, 0)
        
        total = len(statuses)
        success_count = sum(1 for s in statuses if s == STATUS_SUCCESS or s == STATUS_DRYRUN)
        fail_count = sum(1 for s in statuses if s == STATUS_FAIL)
        skipped_count = sum(1 for s in statuses if s == STATUS_SKIPPED)
        
        # Determine overall result message based on success rate
        if fail_count == 0 and skipped_count == 0:
            result_msg = "All jobs completed successfully."
            color_pair = curses.color_pair(1)  # Green
        elif success_count > 0:
            result_msg = f"Completed with {fail_count} failure(s) and {skipped_count} skip(s)."
            color_pair = curses.color_pair(3)  # Yellow (warning)
        else:
            result_msg = "All jobs failed."
            color_pair = curses.color_pair(2)  # Red
        
        lines = [
            "",
            "  ════════════════════════════════════════",
            f"  Total: {total} | Success: {success_count} | Failed: {fail_count} | Skipped: {skipped_count}",
            f"  Result: {result_msg}",
            "  ════════════════════════════════════════",
            "",
        ]
        
        # Use colored message for result line
        msg_line_idx = 3
        
        for i, line in enumerate(lines):
            try:
                if i == msg_line_idx:
                    summary_win.addstr(h // 2 - len(lines) // 2 + i, (w - len(line)) // 2, line, color_pair | curses.A_BOLD)
                else:
                    summary_win.addstr(h // 2 - len(lines) // 2 + i, (w - len(line)) // 2, line)
            except curses.error:
                pass
        
        # Add failed servers list if any failures occurred
        if fail_count > 0 and objects is not None:
            try:
                extra_lines = ["", "Failed servers:", ""]
                for idx, status in enumerate(statuses):
                    if status == STATUS_FAIL:
                        server_name = objects[idx] if idx < len(objects) else 'unknown'
                        failed_line = f"    [✖] {server_name}"
                        extra_lines.append(failed_line)
                
                # Position below main summary
                start_y = h // 2 - len(lines) // 2 + len(lines) + 1
                for i, line in enumerate(extra_lines):
                    try:
                        summary_win.addstr(start_y + i, (w - len(line)) // 2, line, curses.color_pair(2))
                    except curses.error:
                        pass
            except Exception:
                pass
        
        summary_win.refresh()
        print("\nPress any key to exit...")
        
        stdscr.getch()
    finally:
        if _nodelay_active:
            stdscr.nodelay(True)


# --- Dry Run Mode ---

def run_dry_run(objects, command):
    """Dry-run mode: display all commands that would be executed without running them."""
    print(f"\n{'='*70}")
    print(f"  DRY RUN MODE - No commands will be executed")
    print(f"{'='*70}\n")
    print(f"Command: {command}")
    print(f"Servers ({len(objects)}):")
    
    for i, obj in enumerate(objects, 1):
        quoted_obj = shlex.quote(obj)
        cmd_str = command.replace(PLACEHOLDER, quoted_obj)
        
        # Color output (basic ANSI)
        reset = "\033[0m"
        cyan = "\033[96m"
        green = "\033[92m"
        
        marker = f"{green}~{reset}"  # Dry-run marker
        
        print(f"\n  {i}. {marker} {obj}")
        print(f"     {cmd_str}")
    
    print(f"\n{'='*70}")
    print(f"  Total: {len(objects)} command(s) would be executed")
    print(f"{'='*70}\n")


# --- Command Execution ---

def execute_command(obj, command, console=None, use_shell=True):
    """Execute a single command for the given object.

    Returns (status, returncode, output_lines). returncode is None when the
    command could not be launched (e.g. FileNotFoundError).

    stdout and stderr are merged so both appear in order. When a `console` is
    given, each line is appended live and scroll keys are polled between lines
    (via select), so the pane stays responsive even while a command is idle.
    """
    quoted_obj = shlex.quote(obj)
    cmd_str = command.replace(PLACEHOLDER, quoted_obj)

    try:
        process = subprocess.Popen(
            cmd_str,
            shell=use_shell,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        output_lines = []
        stdout = process.stdout

        while True:
            ready, _, _ = select.select([stdout], [], [], 0.1)
            if ready:
                line = stdout.readline()
                if line == '':
                    break  # EOF
                text = line.rstrip('\n')
                output_lines.append(text)
                if console is not None:
                    console.append(text)
            elif console is not None:
                # No output yet: keep the pane responsive to scroll keys.
                console.pump_input()

        process.wait()

        if process.returncode == 0:
            return STATUS_SUCCESS, process.returncode, output_lines
        else:
            return STATUS_FAIL, process.returncode, output_lines

    except FileNotFoundError as e:
        return STATUS_FAIL, None, [f"Command not found: {e.filename}"]
    except Exception as e:
        return STATUS_FAIL, None, [f"Error: {str(e)}"]


# --- Parallel Execution (Optional) ---

def run_parallel(objects, command, max_workers=4):
    """Execute commands in parallel across multiple servers.

    Returns a dict: index -> (status, returncode, output_lines).
    """
    import concurrent.futures

    results = {}  # index -> (status, returncode, output_lines)

    print(f"\n{'='*70}")
    print(f"  PARALLEL MODE - {max_workers} workers")
    print(f"{'='*70}\n")

    def worker(index, obj):
        """Worker function for parallel execution."""
        status, returncode, output = execute_command(obj, command, use_shell=True)
        return index, status, returncode, output

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(worker, i, obj): i for i, obj in enumerate(objects)}

        # Print progress while waiting
        completed = 0
        total = len(futures)
        print("Running commands in parallel...\n")

        for future in concurrent.futures.as_completed(futures):
            idx = futures[future]
            _, status, returncode, output = future.result()
            results[idx] = (status, returncode, output)
            completed += 1

            marker = "✔" if status == STATUS_SUCCESS else "✖"
            print(f"  [{completed}/{total}] {marker} {objects[idx]}")

    return results


# --- Result Collection & Persistent Report ---

def build_records(objects, statuses, returncodes, outputs):
    """Combine the per-object tracking arrays into a list of result records.

    Each record is a dict: {object, status, returncode, output, reason}.
    """
    records = []
    for i, obj in enumerate(objects):
        status = statuses[i]
        output = outputs[i]
        records.append({
            "object": obj,
            "status": status,
            "returncode": returncodes[i],
            "output": output,
            "reason": failure_reason(output) if status == STATUS_FAIL else "",
        })
    return records


def print_report(records):
    """Print a persistent report to stdout after the TUI has been torn down.

    Dumps each object's full captured output followed by a pass/fail summary,
    so the whole run remains in the terminal's scrollback. Failed objects are
    listed with their exit code and the reason (last meaningful output line).
    """
    success = [r for r in records if r["status"] == STATUS_SUCCESS]
    failed = [r for r in records if r["status"] == STATUS_FAIL]
    skipped = [r for r in records if r["status"] == STATUS_SKIPPED]

    print(f"\n{'='*70}")
    print("  BATCH EXECUTOR - RUN OUTPUT")
    print(f"{'='*70}")

    for r in records:
        if r["status"] == STATUS_SUCCESS:
            tag = "[OK]"
        elif r["status"] == STATUS_FAIL:
            rc = r["returncode"]
            tag = f"[FAILED rc={rc}]" if rc is not None else "[FAILED]"
        elif r["status"] == STATUS_SKIPPED:
            tag = "[SKIPPED]"
        else:
            tag = "[PENDING]"

        print(f"\n=== {r['object']}  {tag} ===")
        for line in r["output"]:
            print(f"  {strip_ansi(line)}")

    print(f"\n{'='*70}")
    print(f"  Total: {len(records)} | OK: {len(success)} | "
          f"FAILED: {len(failed)} | SKIPPED: {len(skipped)}")
    print(f"{'='*70}")

    if failed:
        print("\nFailed servers:")
        name_width = max(len(r["object"]) for r in failed)
        for r in failed:
            rc = r["returncode"]
            rc_str = f"rc={rc}" if rc is not None else "rc=?"
            print(f"  [✖] {r['object'].ljust(name_width)}  {rc_str}  {r['reason']}")

    if skipped:
        print(f"\nSkipped (not run): {', '.join(r['object'] for r in skipped)}")

    if not failed:
        print("\nAll jobs completed successfully.")
    print()


# --- TUI Main Loop ---

def tui_main(stdscr, args):
    """The main function to run the TUI."""
    # Register signal handlers for clean shutdown
    original_sigint = signal.getsignal(signal.SIGINT)
    original_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        return _tui_main_impl(stdscr, args)
    finally:
        # Restore original signal handlers
        signal.signal(signal.SIGINT, original_sigint)
        signal.signal(signal.SIGTERM, original_sigterm)


def _tui_main_impl(stdscr, args):
    """Internal TUI implementation (called from tui_main with cleanup)."""
    
    # --- Curses Setup ---
    global _nodelay_active
    curses.curs_set(0)  # Hide the cursor
    stdscr.nodelay(True)  # Non-blocking getch
    stdscr.keypad(True)   # Decode arrow / page / home / end keys
    _nodelay_active = True
    curses.start_color()
    curses.init_pair(1, curses.COLOR_GREEN, curses.COLOR_BLACK)   # Success
    curses.init_pair(2, curses.COLOR_RED, curses.COLOR_BLACK)     # Fail
    curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLACK)  # Prompt
    curses.init_pair(4, curses.COLOR_YELLOW, curses.COLOR_BLACK)  # Skipped
    curses.init_pair(5, curses.COLOR_CYAN, curses.COLOR_BLACK)    # Dry-run
    
    # --- Read Objects ---
    try:
        with open(args.list, 'r') as f:
            objects = [line.strip() for line in f if line.strip()]
        if not objects:
            raise ValueError("Object list file is empty.")
    except (IOError, ValueError) as e:
        return f"Error: Cannot read or parse object list file.\n{e}"
    
    # --- Dry Run Mode ---
    if args.dry_run:
        run_dry_run(objects, args.command)
        return "Dry run completed."
    
    statuses = [STATUS_PENDING] * len(objects)
    returncodes = [None] * len(objects)
    outputs = [[] for _ in objects]
    scroll_offset = 0

    # Handle parallel mode
    if args.parallel and args.parallel > 1:
        results = run_parallel(objects, args.command, max_workers=args.parallel)

        for idx, (status, returncode, output_lines) in results.items():
            statuses[idx] = status
            returncodes[idx] = returncode
            outputs[idx] = output_lines

        show_summary(stdscr, statuses, objects)
        return build_records(objects, statuses, returncodes, outputs)

    # --- Window Layout (created once; the output pane is a continuous log) ---
    h, w = stdscr.getmaxyx()
    list_w = max(w // 4, 20)
    output_w = w - list_w
    if list_w < 10 or output_w < 30:
        print(f"\nWarning: Terminal too small for proper display. "
              f"Consider using --plain mode.", file=sys.stderr)
    try:
        list_win = stdscr.subwin(h, list_w, 0, 0)
        output_win = stdscr.subwin(h, output_w, 0, list_w)
        output_content_win = output_win.derwin(h - 2, output_w - 2, 1, 1)
    except curses.error:
        print(f"\nError: Could not create windows. Terminal may be too small.", file=sys.stderr)
        return "Window creation failed."

    list_h = list_win.getmaxyx()[0]
    visible_lines = max(list_h - 2, 1)
    console = OutputConsole(stdscr, output_content_win)
    draw_title(output_win, "Output  (Up/Down PgUp/PgDn End=follow)")

    # --- Main Sequential Loop ---
    aborted = False
    autoskip = False  # When set, failures are skipped without prompting.

    for i, obj in enumerate(objects):
        if aborted:
            statuses[i] = STATUS_SKIPPED
            continue

        # Keep the active object visible in the (scrolling) left list.
        if i >= scroll_offset + visible_lines:
            scroll_offset = i - visible_lines + 1
        if i < scroll_offset:
            scroll_offset = i
        draw_list(list_win, objects, statuses, i, scroll_offset)

        # Append a header for this object to the continuous log.
        console.append("", f"=== {obj} ===")

        # --- Execute Command ---
        try:
            sleep(0.1)  # Small delay before starting
            status, returncode, output_lines = execute_command(obj, args.command, console)
            statuses[i] = STATUS_SUCCESS if status == STATUS_SUCCESS else STATUS_FAIL
            returncodes[i] = returncode
            outputs[i] = output_lines
        except AbortSignal:
            # User asked to abort mid-command; remaining objects are marked
            # SKIPPED by the top-of-loop guard.
            aborted = True
            continue
        except Exception as e:
            statuses[i] = STATUS_FAIL
            outputs[i] = [f"Error: {e}"]
            console.append(f"Error: {e}")

        # Note the outcome inline in the log, then refresh the status list.
        rc = returncodes[i]
        if statuses[i] == STATUS_SUCCESS:
            console.append(f"--- {obj}: OK (rc={rc}) ---")
        else:
            console.append(f"--- {obj}: FAILED (rc={rc}) ---")
        draw_list(list_win, objects, statuses, i, scroll_offset)

        # --- Handle Failure ---
        if statuses[i] == STATUS_FAIL:
            if autoskip:
                # Auto-skip mode: keep going without prompting.
                console.append(f"(auto-skip) continuing after failure on '{obj}'")
                continue
            try:
                choice = show_prompt(
                    stdscr, f"Failed on '{obj}'. (s)kip, (a)utoskip rest, or (e)xit?", console)
                if choice in [ord('s'), ord('S')]:
                    continue  # Skip to next
                elif choice in [ord('a'), ord('A')]:
                    # Skip this and every subsequent failure without prompting.
                    autoskip = True
                    console.append("(auto-skip enabled) remaining failures will be skipped")
                    continue
                elif choice in [ord('e'), ord('E')]:
                    # Stop launching new commands; remaining objects become
                    # SKIPPED so the final report still accounts for them.
                    aborted = True
                    continue
            except Exception:
                pass

    # --- End of Execution ---
    records = build_records(objects, statuses, returncodes, outputs)
    # Append the summary into the log, then let the user scroll the whole run.
    console.append("", *summary_lines(records))
    draw_title(output_win, "Review  (Up/Down PgUp/PgDn Home/End, q to quit)")
    console.review()
    return records


def summary_lines(records):
    """Build the run-summary block (list of strings) shown in-pane and reused
    by the persistent terminal report."""
    success = [r for r in records if r["status"] == STATUS_SUCCESS]
    failed = [r for r in records if r["status"] == STATUS_FAIL]
    skipped = [r for r in records if r["status"] == STATUS_SKIPPED]

    lines = [
        "=" * 50,
        f"SUMMARY  Total: {len(records)} | OK: {len(success)} | "
        f"FAILED: {len(failed)} | SKIPPED: {len(skipped)}",
        "=" * 50,
    ]
    if failed:
        name_width = max(len(r["object"]) for r in failed)
        lines.append("Failed servers:")
        for r in failed:
            rc = r["returncode"]
            rc_str = f"rc={rc}" if rc is not None else "rc=?"
            lines.append(f"  [x] {r['object'].ljust(name_width)}  {rc_str}  {r['reason']}")
    if skipped:
        lines.append(f"Skipped (not run): {', '.join(r['object'] for r in skipped)}")
    if not failed:
        lines.append("All jobs completed successfully.")
    return lines


# --- Plain Text Mode (Fallback for small terminals) ---

def plain_mode(objects, command):
    """Plain text mode output for terminals that don't support curses."""
    import time
    
    print(f"\n{'='*70}")
    print("  BATCH EXECUTOR - PLAIN TEXT MODE")
    print(f"{'='*70}\n")
    
    results = []
    
    for i, obj in enumerate(objects, 1):
        status_marker = "▶"
        print(f"[{i}/{len(objects)}] {status_marker} {obj}")
        
        quoted_obj = shlex.quote(obj)
        cmd_str = command.replace(PLACEHOLDER, quoted_obj)
        print(f"       Command: {cmd_str}")
        
        start_time = time.time()
        try:
            process = subprocess.Popen(
                cmd_str,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
            
            for line in iter(process.stdout.readline, ''):
                print(f"       {line.rstrip()}")
            
            process.wait()
            elapsed = time.time() - start_time
            
            if process.returncode == 0:
                status_marker = "✔"
                results.append(STATUS_SUCCESS)
            else:
                status_marker = "✖"
                results.append(STATUS_FAIL)
            
        except Exception as e:
            status_marker = "✖"
            results.append(STATUS_FAIL)
        
        print(f"       Status: {status_marker} (completed in {elapsed:.2f}s)\n")
    
    # Summary
    success_count = sum(1 for s in results if s == STATUS_SUCCESS)
    fail_count = sum(1 for s in results if s == STATUS_FAIL)
    
    print(f"{'='*70}")
    print(f"  SUMMARY: {len(results)} total | {success_count} succeeded | {fail_count} failed")
    print(f"{'='*70}\n")


# --- Argument Parser and Entry Point ---

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="A TUI wrapper to execute a command over a list of objects.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=f'''
Example Usage:
  ./executor.py --list servers.txt --command 'ssh $object "echo $HOSTNAME ; df -h /"'

Options:
  --dry-run           Preview commands without executing them
  --plain             Use plain text output instead of TUI (fallback mode)
  --parallel N        Run commands in parallel with N workers (default: sequential)

Details:
  - The command string must contain the placeholder '{PLACEHOLDER}'.
  - The script uses `shlex` to parse the command, which prevents the local
    shell from expanding variables like `$HOSTNAME`. Variables will be correctly
    expanded on the remote machine.
  - Object names with spaces or special characters are handled automatically.
  - Press 'q' during a command's execution to abort the entire script.

Dry Run Example:
  ./executor.py --list servers.txt --command 'ssh $object "uptime"' --dry-run
'''
    )
    parser.add_argument(
        "-l", "--list",
        required=True,
        help="Path to a text file containing a list of objects (one per line)."
    )
    parser.add_argument(
        "-c", "--command",
        required=True,
        help=f"The command to execute. Use '{PLACEHOLDER}' as a placeholder for the object."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Preview commands without executing them (safe mode)."
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        default=False,
        help="Use plain text output instead of TUI (for small terminals)."
    )
    parser.add_argument(
        "-p", "--parallel",
        type=int,
        metavar="N",
        default=1,
        help="Run commands in parallel with N workers (default: 1 for sequential)."
    )

    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(1)

    args = parser.parse_args()

    # Validate list file exists
    if not os.path.exists(args.list):
        print(f"Error: The file '{args.list}' does not exist.", file=sys.stderr)
        sys.exit(1)

    # Validate placeholder in command (unless dry-run)
    if PLACEHOLDER not in args.command and not args.dry_run:
        print(f"Error: The command string must include the placeholder '{PLACEHOLDER}'.", file=sys.stderr)
        sys.exit(1)

    # Check terminal size for TUI mode
    if not args.plain and not args.dry_run:
        check_terminal_size()

    try:
        with open(args.list, 'r') as f:
            objects = [line.strip() for line in f if line.strip()]
        
        if not objects:
            print("Error: Object list file is empty.", file=sys.stderr)
            sys.exit(1)
        
        # Dry-run mode (no curses needed)
        if args.dry_run:
            run_dry_run(objects, args.command)
        # Plain text mode (fallback for small terminals)
        elif args.plain:
            plain_mode(objects, args.command)
        else:
            result = curses.wrapper(tui_main, args)
            if isinstance(result, list):
                # Sequential/parallel run returned structured records: print the
                # persistent report into the terminal's scrollback.
                print_report(result)
            elif result:
                # Early exit (e.g. window creation failed) returned a message.
                print(result)
    except curses.error as e:
        print(f"A terminal error occurred: {e}")
        print("Please ensure your terminal is large enough and supports colors.")
        print("Try running with --plain for text-only output.", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nExecution cancelled by user.")
