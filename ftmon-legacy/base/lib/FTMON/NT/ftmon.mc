; ;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
;                    FTMON (Fast Track Systems Monitor)
;
;   Script: @(#) $RCSfile: ftmon.mc,v $ 
; 
;   DESCRIPTION: 
;
;   @(#) FTMON is a free extensable systems monitor that can be
;   @(#) integrated to forward events to a number of free and commercial
;   @(#) event management systems.
;
;   $Source: /cvsroot/ftmon/base2/lib/FTMON/NT/ftmon.mc,v $
;
;   $Date: 2003/01/10 13:11:06 $
;
;   @(#) $Revision: 1.1.1.1 $
;    
;
;   Copyright (C) 2001  Danny Sheehan
;
;   This program is free software; you can redistribute it and/or modify
;   it under the terms of the GNU General Public License as published by
;   the Free Software Foundation; either version 2 of the License, or
;   (at your option) any later version.
;
;   This program is distributed in the hope that it will be useful,
;   but WITHOUT ANY WARRANTY; without even the implied warranty of
;   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
;   GNU General Public License for more details.
;
;   You should have received a copy of the GNU General Public License
;   along with this program; if not, write to the Free Software
;   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
;
;      FTMON
;      PO Box 223
;      Sydney NSW 2001
;      AUSTRALIA
;      dsheehan@ftmon.org
;      http://ftmon.org
;
; ;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
MessageIdTypedef=DWORD
LanguageNames=(English=0x409:MSG00409)


MessageId=0x1
SymbolicName=MSG_FTMON_STARTING
Facility=Application
Language=English
FTMON Started %r
DEBUG   = %1 %r
INTERVAL= %2 secs %r
BASE_DIR= %3 %r
CFG_DIR = %4 %r
HTML_DIR= %5 %r
LOG_DIR = %6 %r
MON_PATH= %7 %r
MON_ARGS= %8 %r
.

MessageId=0x2
SymbolicName=MSG_FTMON_STOPPING
Facility=Application
Language=English
FTMON Stopping %r
PID=%1
.

MessageId=0x3
SymbolicName=MSG_FTMON_HEARTBEAT
Facility=Application
Language=English
FTMON Heartbeat %r
PID=%1 %r
INTERVAL=%2 %r
RUNTIME=%3 %r
.

MessageId=0x4
SymbolicName=MSG_FTMON_INTERNAL_ERROR
Facility=Application
Language=English
FTMON Error (mail help@ftmon.org) %r
COMPONENT: %1 %r
LINE: %2 %r
MESSAGE: %3 %r
.

MessageId=0x5
SymbolicName=MSG_FTMON_LOGGED_EVENT
Facility=Application
Language=English
%1 : %2 : %3 : %4 : %5
.

