#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Rich featured serial console like cuteterm, but with a fancy ncurses-TUI.

# TODO:
# - line-editing
# - set default line-ending or encoding on commandline? Autodetect?
# - Better resize (redraw content instead of erasing)

# https://docs.python.org/3/howto/curses.html

import os
import sys
import glob
import curses  # https://docs.python.org/3/library/curses.html
import argparse  # https://docs.python.org/3/library/argparse.html
import serial  # https://pyserial.readthedocs.io/
# import serdummy as serial
import styles
import finput
import widgets
import translate


def launch():
    seroptions = [  # [short, long, default, help]
        ["-r", "--baudrate", 9600, "Baud rate such as 9600 (default) or 115200 etc."],
        ["-p", "--param", "8,N,1", """B,P,S
        <B>-bytesize: Number of data bits. Possible values: 5-8
        <P>-parity: Enable parity checking. Possible values:
                    N (None), E (Even), O (Odd) M (Mark), S (Space)
        <S>-stopbits: Number of stop bits. Possible values: 1, 1.5, 2
        Default is 8,N,1"""],
        ["-t", "--timeout", 0.05, "Set a read timeout value. Default is 0.05"],
        ["-x", "--xonxoff", False, "Enable software flow control"],
        ["-c", "--rtscts", False, "Enable hardware (RTS/CTS) flow control"],
        ["-d", "--dsrdtr", False, "Enable hardware (DSR/DTR) flow control"],
        ["-n", "--nonexclusive", False, """Don't Set exclusive access mode (POSIX only).
        A port cannot be opened in exclusive access mode if it is already open in
        exclusive access mode."""]
    ]
    otheroptions = [
        ["-da", "--dumpall", "", "record both received and sent bytes in one file"],
        ["-dr", "--dumprec", "", "record received bytes to a file"],
        ["-ds", "--dumpsnd", "", "record sent bytes to a file"],
        ["-s", "--padsize", 2000, "number of lines to keep in scrollback-pad (max. 32767, default 2000)"],
        ["-m", "--maxfilehex", 4096, """when uploading a file, only files smaller then this are displayed in the
        hexdump. For larger files, a placeholder is shown. (max. 16 * padsize, default 4 KiB)"""]
    ]

    # OK, so lets go parsing
    parser = argparse.ArgumentParser()
    parser.add_argument("port", help="Device name or None (default: /dev/ttyUSB0)", default="/dev/ttyUSB0", nargs="?")
    for ao in seroptions + otheroptions:
        if type(ao[2]) == bool:
            parser.add_argument(ao[0], ao[1], action="store_true", help=ao[3])
        else:
            parser.add_argument(ao[0], ao[1], type=type(ao[2]), default=ao[2], help=ao[3])

    # hier noch weitere options...
    args = parser.parse_args()
    # extract serial device options
    serdev = dict(x for x in vars(args).items() if x[0] in map(lambda x: x[1][2:], seroptions))
    # add port
    serdev["port"] = args.port
    # parse byteesize,parity,stop
    try:
        b, p, s = serdev["param"].split(",")
        serdev.pop("param")
        serdev["bytesize"] = int(b)
        serdev["parity"] = p.upper()
        serdev["stopbits"] = float(s)
    except Exception as e:
        print("ERROR:", e, type(e))
        try:
            eno = e.errno
        except:
            eno = 100
        return eno

    # rename exclusive
    serdev["exclusive"] = not serdev["nonexclusive"]
    serdev.pop("nonexclusive")

    # validate / set other options
    args.padsize = min(32767, args.padsize)
    widgets.TxtWin.PADSIZE = args.padsize
    args.maxfilehex = min(16 * args.padsize, args.maxfilehex)

    # Start!
    try:
        curses.wrapper(tuimain, Iserial(serdev, args), args)
    except Exception as e:
        print("ERROR:", e, type(e))
        try:
            eno = e.errno
        except:
            eno = 100
        return eno


class Iserial():
    """wrap serial read and write functions for logging"""

    def __init__(self, serdev, options):
        self.ser = serial.Serial(**serdev)
        # open dump-files, if any
        self.dumps = {
            "snd": open(options.dumpsnd, "ab") if options.dumpsnd else None,
            "rec": open(options.dumprec, "ab") if options.dumprec else None,
            "all": open(options.dumpall, "ab") if options.dumpall else None
        }

    def read(self):
        rx = self.ser.read()
        while self.ser.inWaiting() > 0: rx += self.ser.read(self.ser.inWaiting())
        if self.dumps["rec"]:
            self.dumps["rec"].write(rx)
            self.dumps["rec"].flush()
        if self.dumps["all"]:
            self.dumps["all"].write(rx)
            self.dumps["all"].flush()
        return rx

    def write(self, s):
        self.ser.write(s)
        self.ser.flush()
        if self.dumps["snd"]:
            self.dumps["snd"].write(s)
            self.dumps["snd"].flush()
        self.ser.flush()
        if self.dumps["all"]:
            self.dumps["all"].write(s)
            self.dumps["all"].flush()


def tuimain(scr, ser, options):
    #### INIT CURSES, STYLES, GUI ####
    scr.clear()
    scr.nodelay(True)
    scr.getch()

    widgets.COLOR = styles.CURRENT(curses)
    gui = widgets.Gui()
    gui.message("Connected to %s (%d baud, %s)\n\n" % (options.port, options.baudrate, options.param))

    #### MAIN LOOP ####
    while gui.running:
        gui.in_str.win.refresh()
        c = scr.getch()
        ins = gui.tInp.getState()

        if c == -1 and ser:  # no input: poll/read serial
            rx = ser.read()
            if len(rx) > 0: gui.show(rx, "text")

        elif 31 < c < 127:  # ASCII
            gui.in_str.append(chr(c), ins == "Hex")

        elif 0b11000000 <= c <= 0b11110000 and ins != "Hex":  # UTF-8 multibyte
            cp = bytearray([c])
            c = scr.getch()
            while 0b10000000 <= c <= 0b11000000:
                cp.append(c)
                c = scr.getch()
            try:
                gui.in_str.append(cp.decode("utf-8"))
            except:
                gui.message("Invalid Input: " + str(cp) + "\n")

        elif c == 10:  # ENTER
            inp = gui.in_str.inp
            end = gui.n2brk[gui.tBrk.getState()]
            if ins == "Text":
                s = (inp + end)
                gui.show(s, "send")
                # trancode to current CP
                if ser: ser.write(s.encode(gui.tAsc.getState(), "ignore"))
                gui.in_str.clear()
            elif ins == "File":
                f = finput.tryget(inp)
                gui.message(f[1] + "\n") if f[2] else gui.message(f[1] + "\n", "error")
                PROWIDTH = 40
                if f[0]:
                    s = f[0].read()
                    if ser:
                        # send in chunks to display progress
                        chks = len(s) // (PROWIDTH - 1)
                        gui.message("░" * PROWIDTH + "\r", "send")
                        for i in range(PROWIDTH):
                            ser.write(s[(i * chks):((i + 1) * chks)])
                            gui.message("▓", "send")
                        gui.message("\n")

                    if len(s) <= options.maxfilehex:
                        gui.hexdump(s, "send")
                    else:  # don't relay dump it
                        gui.hexdump("[BINARY DATA %d BYTES]".encode("ASCII") % len(s), "send")
                    gui.message("Transmission done.\n")
                    f[0].close()
                gui.in_str.clear()
            elif ins == "Hex":
                try:
                    bafh = bytearray.fromhex(inp.upper())
                    gui.show(bafh, "send")
                    if ser: ser.write(bafh)
                    gui.in_str.clear()
                except ValueError:
                    gui.message("Invalid Hex-string\n", "error")

        elif c == 9 and ins == "File":  # Tab-completion
            matches = glob.glob(gui.in_str.inp + "*")
            if len(matches) == 1:
                gui.in_str.inp = matches[0]
                if os.path.isdir(matches[0]): gui.in_str.inp += os.path.sep
                gui.in_str.redraw()
            else:
                res = finput.sortedginfo(matches)
                gui.message("\n\n----- " + gui.in_str.inp + "* -----\n\n" + "\n".join(res))

        elif c in (127, curses.KEY_BACKSPACE):
            gui.in_str.backspace(ins == "Hex")
        elif c == curses.KEY_UP:
            gui.in_str.goHistory(1)
        elif c == curses.KEY_DOWN:
            gui.in_str.goHistory(-1)

        elif c == curses.KEY_NPAGE:
            gui.bscroll("down")
        elif c == curses.KEY_PPAGE:
            gui.bscroll("up")
        elif c == curses.KEY_HOME:
            gui.bscroll("home")
        elif c == curses.KEY_END:
            gui.bscroll("end")

        elif c in gui.keys:
            gui.keys[c].action()
        elif c in gui.toggles:
            gui.toggles[c].nextState()

        elif c == curses.KEY_RESIZE:
            # TODO: gracefully resize.
            # Starting over / clearing everything is ugly.
            curses.LINES, curses.COLS = scr.getmaxyx()
            gui = widgets.Gui()

        else:
            gui.message("Unmapped key %d\n" % c)


if __name__ == '__main__':
    sys.exit(launch())
