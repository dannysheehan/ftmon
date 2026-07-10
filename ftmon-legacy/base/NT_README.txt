RELEASE NOTES - FAST TRACK SYSTEMS MONITOR (MUM RELEASE) 
========================================================

mail dsheehan@ftmon.org if you have any problems or just want to say hi.

You can join ftmon-announce@yahoogroups.com to be on the mailing list for
announcements of new versions of FTMON.


CHANGES SINCE LAST RELEASE
==========================
Massive changes - Too numerous to number.


TO INSTALL
==========
Just run the msi installer - make sure you install at least one config file
I suggest the base NT monitor files.


VIEWING
=======
To see the pretty pictures (wait a minute or two).
cd c:\program files\ftmon\base\html (or wherever you installed FTMON)
index.html

Then drill down from product -> monitor -> rrd displays.


NETGEAR
=======
If you are using FTMON to monitor your RT314 router you will need to edit
line 29,30 of c:\program files\ftmon\base\cfg\Netgear\RT314\impl\base.cfg 
and insert the ip address and password of the telnet session to your router.

e.g. by default Netgear set the following as defaults. 
address = "192.168.0.1"
password = "1234");

NB You don't have to change
anything if you have been very naughty and havn't change your router defaults.

NETGEAR
=======
Ditto as for NETGEAR.

DYNDNS
======
For DYNDNS support the ip addresses of the RT314 router are written to 
files corresponding to the interface names in the root directory.
c:\wanif0
c:\enif0
You can change this be editing line 29 of c:\ftmon\nt\netgear\rt314\base.cfg
e.g. /$Interface ->   /my/new/directory/$Interface


CHANGING THRESHOLDS
===================
The first lot of thesholds you want to change are the disk ones
notepad c:\ftmon\cfg\microsoft\nt\disk.cfg
then adjust the numbers in 
( $AvialMB < 768.5450 ) - WARNING
( $AvailMB < 684.0400 ) - MINOR
( $AvailMB < 598.5350)  - CRITICAL

Duplicate these three lines for other disks you might have and adjust 
Alternatevly replace the 'c:\\' with the following regular expression
'/.*/' and the thresholds will apply to all drives that are discovered.
i.e.
[ '/.*/', '( $AvailMB < 769.5450 )', $FT::ESEV[2],  $DISK_ID,  $DISK_MSG ];

[ '/.*/', '( $AvailMB < 684.0400 )',  $FT::ESEV[4],   $DISK_ID,  $DISK_MSG ];

[ '/.*/', '( $AvailMB < 598.5350 )', $FT::ESEV[6],  $DISK_ID,  $DISK_MSG ];


If you are really brave uncomment out the DISCOVER code at the bottom of the
config file. It will go and set the thresholds for you based on the minimum
disk space values reached for each drive (plus 10, 20, 30 pct buffer) over the
last 2000 minutes. NB It has known memory leak problems.


IT WONT'T START ?
-----------------
1. First place to look is the NT application log. Look at any events with
source FTMON.

2. Check the ftmon_install.log file in the %TEMP%\logs directory. 

3. Look at the Info line in the NT application log & run the monitor manually
e.g. open a command prompt
set BASE_DIR="\Program Files\FTMON\base"
"\Program Files\FTMON\base\bin\ftmon.pl" -h "\Program Files\FTMON ...
There is a ftmon_start.cmd in the base directory for doing this.

3. Check the ftmon.log file in the %BASE_DIR%\logs directory


4. Turn on FTMON logging by editing the ftmon.cfg file in the cfg directory (put a 1 next to everything).
Then try restarting.


NOTES
=====
1. Be a patient when FTMON starts up for the first it may take some time for the
monitors to show some values depending on their monitoring intervals.
2. There are leaks in the perfmon stuff - this is slowly being addressed
So it would pay to regularly 
net stop ftmonsvc; net start ftmonsvc
