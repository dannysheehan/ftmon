 ;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
                    FTMON (Fast Track Systems Monitor)

   Script: @(#) $RCSfile: ftmon.h,v $ 
 
   DESCRIPTION: 

   @(#) FTMON is a free extensable systems monitor that can be
   @(#) integrated to forward events to a number of free and commercial
   @(#) event management systems.

   $Source: /cvsroot/ftmon/base2/lib/FTMON/NT/ftmon.h,v $

   $Date: 2003/01/10 13:11:06 $

   @(#) $Revision: 1.1.1.1 $
    

   Copyright (C) 2001  Danny Sheehan

   This program is free software; you can redistribute it and/or modify
   it under the terms of the GNU General Public License as published by
   the Free Software Foundation; either version 2 of the License, or
   (at your option) any later version.

   This program is distributed in the hope that it will be useful,
   but WITHOUT ANY WARRANTY; without even the implied warranty of
   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
   GNU General Public License for more details.

   You should have received a copy of the GNU General Public License
   along with this program; if not, write to the Free Software
   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

      FTMON
      PO Box 223
      Sydney NSW 2001
      AUSTRALIA
      dsheehan@ftmon.org
      http://ftmon.org

 ;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
//
//  Values are 32 bit values layed out as follows:
//
//   3 3 2 2 2 2 2 2 2 2 2 2 1 1 1 1 1 1 1 1 1 1
//   1 0 9 8 7 6 5 4 3 2 1 0 9 8 7 6 5 4 3 2 1 0 9 8 7 6 5 4 3 2 1 0
//  +---+-+-+-----------------------+-------------------------------+
//  |Sev|C|R|     Facility          |               Code            |
//  +---+-+-+-----------------------+-------------------------------+
//
//  where
//
//      Sev - is the severity code
//
//          00 - Success
//          01 - Informational
//          10 - Warning
//          11 - Error
//
//      C - is the Customer code flag
//
//      R - is a reserved bit
//
//      Facility - is the facility code
//
//      Code - is the facility's status code
//
//
// Define the facility codes
//


//
// Define the severity codes
//


//
// MessageId: MSG_FTMON_STARTING
//
// MessageText:
//
//  FTMON Started %r
//  DEBUG   = %1 %r
//  INTERVAL= %2 secs %r
//  BASE_DIR= %3 %r
//  CFG_DIR = %4 %r
//  HTML_DIR= %5 %r
//  LOG_DIR = %6 %r
//  MON_PATH= %7 %r
//  MON_ARGS= %8 %r
//
#define MSG_FTMON_STARTING               ((DWORD)0x00000001L)

//
// MessageId: MSG_FTMON_STOPPING
//
// MessageText:
//
//  FTMON Stopping %r
//  PID=%1
//
#define MSG_FTMON_STOPPING               ((DWORD)0x00000002L)

//
// MessageId: MSG_FTMON_HEARTBEAT
//
// MessageText:
//
//  FTMON Heartbeat %r
//  PID=%1 %r
//  INTERVAL=%2 %r
//  RUNTIME=%3 %r
//
#define MSG_FTMON_HEARTBEAT              ((DWORD)0x00000003L)

//
// MessageId: MSG_FTMON_INTERNAL_ERROR
//
// MessageText:
//
//  FTMON Error (mail help@ftmon.org) %r
//  COMPONENT: %1 %r
//  LINE: %2 %r
//  MESSAGE: %3 %r
//
#define MSG_FTMON_INTERNAL_ERROR         ((DWORD)0x00000004L)

//
// MessageId: MSG_FTMON_LOGGED_EVENT
//
// MessageText:
//
//  %1 : %2 : %3 : %4 : %5
//
#define MSG_FTMON_LOGGED_EVENT           ((DWORD)0x00000005L)

