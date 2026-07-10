#############################################################################
#                    FTMON (Fast Track Systems Monitor)
#
#   Script: @(#) $RCSfile: ftmonsvc.pl,v $ 
# 
#   DESCRIPTION: 
#
#   @(#) FTMON is a free extensable systems monitor that can be
#   @(#) integrated to forward events to a number of free and commercial
#   @(#) event management systems. This module encapsultes FTMON in a service.
#
#   $Source: /cvsroot/ftmon/base2/bin/ftmonsvc.pl,v $
#
#   $Date: 2003/01/10 13:09:43 $
#
#   @(#) $Revision: 1.1.1.1 $
#    
#
#   Copyright (C) 2001  Danny Sheehan
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 2 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program; if not, write to the Free Software
#   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
#
#      FTMON
#      PO Box 283
#      Eastwood NSW 2122
#      AUSTRALIA
#      dsheehan@ftmon.org
#      http://ftmon.org
#
#############################################################################
package PerlSvc;


#
# Define service name and service display name.
# these variables cannot be 'my'
#
our $Name        = 'ftmonsvc';
our $DisplayName = 'FastTrack Systems Monitor';


#
# Define the location of the configuration directories & files.
# 
$CONFIG_VEND = "$ENV{'SystemRoot'}/FTMON";
$CONFIG_VEND =~ s/\\/\//g;
$CONFIG_VEND =~ s/\"//g;

$CONFIG_FILE  = "$CONFIG_VEND/ftmon.cfg";

use Win32;
use Win32::EventLog;
use Win32::Process;

$MSG_FTMON_STARTING = 1;
$MSG_FTMON_STOPPING = 2;
$MSG_FTMON_HEARTBEAT = 3;
$MSG_FTMON_INTERNAL_ERROR = 4;
$MSG_FTMON_LOGGED_EVENT = 5;
$LOG_FILE = $ENV{'TEMP'} . '\ftmonsvc.log'; 

# -----------------------------------------------------------------------
sub Startup {
  $CFG_DIR  = "";
  $HTML_DIR = "";
  $LOG_DIR = "";
  $BASE_DIR = "";
  $BASE_TYPE = "";
  $DEBUG    = 1;
  $INTERVAL = 1;



  #
  # Load the configuration file
  #
  if ( -f $CONFIG_FILE ) {
    eval {
      do "$CONFIG_FILE";
    };

    if ( $@ ) {
      logEvent(
          $MSG_FTMON_INTERNAL_ERROR,  
          [$CONFIG_FILE, __LINE__, $@] );
    }
  }

  if ( ! $BASE_DIR  || 
       ! $CFG_DIR ||
       ! $HTML_DIR ||
       ! $LOG_DIR  ) {
    logEvent(
      $MSG_FTMON_INTERNAL_ERROR,  
      [$CONFIG_FILE, __LINE__, "Not configured correctly, please fix."]);
    exit(1);
  }

  my $base_dir = $BASE_DIR;
  $base_dir =~ s/\//\\/g;
  $base_dir =~ s/\"//g;
  $ENV{'BASE_DIR'} = $base_dir;

  my $mon_path =  $base_dir . '\bin\ftmon.exe';
  my $mon_args = 
     "-o \"$HTML_DIR\" " .
     "-p \"$CFG_DIR\" " .
     "-l \"$LOG_DIR\" " .
     "-f \"$LOG_FILE\" " .
     "-v $INTERVAL";



  #
  # M A I N
  #

  logEvent(
    $MSG_FTMON_STARTING,
    [$DEBUG,
    $INTERVAL,
    $BASE_DIR,
    $CFG_DIR,
    $HTML_DIR,
    $LOG_DIR,
    $mon_path,
    $mon_args],
    EVENTLOG_INFORMATION_TYPE);

  my $exit_code;
  my $ProcessObj;
  my $pid;

  if ( ! Win32::Process::Create(
          $ProcessObj,
          "$mon_path",
          "\"$mon_path\" $mon_args", 
          1,
          NORMAL_PRIORITY_CLASS |
          CREATE_NO_WINDOW |
          DETACH_PROCESS,
          $base_dir) ) {
    $exit_code = Win32::GetLastError();
    logEvent(
        $MSG_FTMON_INTERNAL_ERROR,
        ["Win32::Process::Create()", __LINE__,
         $mon_path . " " . $mon_args . 
         " process::create died with $exit_code -" .
         Win32::FormatMessage( $exit_code )] );

    exit($exit_code);
  }
  sleep(3);
  $pid = $ProcessObj->GetProcessID();

  
  my $num_dies = 3;
  while ( ContinueRun() ) {

    # Allow "stop service" to get in, within 1 second
    for ( $i = 0; $i < $INTERVAL && ContinueRun(); $i++ ) {
      $ProcessObj->Wait(1000);
      $exit_code = 0;
      $ProcessObj->GetExitCode($exit_code);

      $num_dies = 3 if ( $exit_code == 259 );


      #
      # Monitor died .
      #
      if ( $exit_code == 0 || $exit_code != 259 ) {

        if ( $exit_code > 0 ) {

          #
          # Monitor died unexpectedly. We don't want to retry restarting 
          # too often as there is probably a reason.
          #
          logEvent(
            $MSG_FTMON_INTERNAL_ERROR,
            ["Win32::Process::GetExitCode()", __LINE__,
             "Monitor died with $exit_code -" .
             Win32::FormatMessage( $exit_code )] );

          $num_dies --;
          if ( ! $num_dies ) {
            exit($exit_code);
          }
        }

        if ( ! Win32::Process::Create(
            $ProcessObj,
            "$mon_path",
            "\"$mon_path\" $mon_args", 
            1,
            NORMAL_PRIORITY_CLASS |
            DETACH_PROCESS |
            CREATE_NO_WINDOW,
            $base_dir) ) {

          $exit_code = Win32::GetLastError();
          logEvent(
            $MSG_FTMON_INTERNAL_ERROR,
            ["Win32::Process::Create()", __LINE__,
             $mon_path . " " . $mon_args . 
             " process::create died with $exit_code -" .
             Win32::FormatMessage( $exit_code )] );

          exit($exit_code);
        }
        $pid = $ProcessObj->GetProcessID();
      }
    }


    unlink($LOG_FILE);

    #logEvent( 
    #   "Heartbeat:\n" .
    #   "INTERVAL=$INTERVAL\n",
    #   EVENTLOG_INFORMATION_TYPE );
  }

  #
  # Monitor ended as a result of service stop.
  #
  $ProcessObj->Kill(0);
  $ProcessObj->Wait(2);


  logEvent($MSG_FTMON_STOPPING, [$pid], EVENTLOG_INFORMATION_TYPE);

} 

# -----------------------------------------------------------------------
sub Install {

  if ( ! defined $ENV{BASE_DIR} ) {
      logEvent(
        $MSG_FTMON_INTERNAL_ERROR,
        [ "Install()",  __LINE__,
          "Please define BASE_DIR environment variable." ]);
      exit(1);
  }

  my $base_dir = $ENV{BASE_DIR};
  $base_dir =~ s/\\/\//g;
  $base_dir =~ s/\"//g;

  #
  # Setup a template configuration file
  #
  if ( ! -d $CONFIG_VEND ) {
    print "Creating $CONFIG_VEND \n";
    if ( ! mkdir($CONFIG_VEND, 0755) ) {
      logEvent(
        $MSG_FTMON_INTERNAL_ERROR,
        [ "mkdir()",  __LINE__,
          "Could not create '$CONFIG_VEND': $!" ]);
      exit(1);
    }
  }

  my $html_dir = "$base_dir/../html";
  $TO_PASS = Win32::LoginName() . "@" . Win32::NodeName();
  $CONFIG_STR = <<EOF;
\$DEBUG    = 1;
\$BASE_DIR = '$base_dir';
\$CFG_DIR  = '$base_dir/../cfg';
\$HTML_DIR = '$html_dir';
\$LOG_DIR  = '$base_dir/../logs';
\$INTERVAL = 61;
EOF

  print "creating $CONFIG_FILE \n";
  if ( ! open(CFG, "> $CONFIG_FILE") ) {
    logEvent(
        $MSG_FTMON_INTERNAL_ERROR,
        [ "mkdir()",  __LINE__,
          "Can not create '$CONFIG_FILE': $!"] );
    exit(1);
  }
      
  print CFG $CONFIG_STR;

  close(CFG);

  system("net start ftmonsvc");
  printf "Started \n";

  #
  # Setup the startup page.
  #
  if ( ! -d $html_dir ) {
    print "creating $html_dir\n";
    mkdir($html_dir, 0755) || die "$html_dir : $!";
  }

  if ( ! -f "$html_dir/index.html" ) {
    print "creating $html_dir/index.html start page. \n";
    open(HTML, "> $html_dir/index.html") || die "$html_dir/index.html : $!";

    print HTML 
"
<HTML>
<meta HTTP-EQUIV=\"Refresh\" CONTENT=\"30\">
<BODY BGCOLOR=\"#FFFFFF\">
&nbsp;
<h1>Welcome To FTMON</h1><p>FTMON is running for the first time. The main FTMON page will appear in this browser window when FTMON starts.</p><p>*** Edit 'D:/WINNT/FTMON/ftmon.cfg' and restart the FTMON service if you require the HTML & logs to be directed
to a different location to the one displayed in the Address bar.
Look at for errors in '$LOG_FILE' if FTMON fails to start.
</p>
</BODY>
</HTML>
";
    close(HTML);
  }

  my $html_start = $base_dir . "/../html/index.html";
  $html_start =~ s/\/\//\\/g;
  $html_start =~ s/\//\\/g;

  print "start ", $ENV{'SystemRoot'} . "\\explorer",  " \"$html_start\"\n";
  system("start", $ENV{'SystemRoot'} . "\\explorer",  $html_start);
}

# -----------------------------------------------------------------------
sub Remove 
{
  
  system("net stop ftmonsvc");

  #
  # Clean out the configuration directory and any backup files etc in it.
  #
  #
  my $file;
  my $file_dir;
  opendir(CFG_DIR, $CONFIG_VEND) || die "Could not open '$CONFIG_VEND'";
  foreach $file ( readdir(CFG_DIR) ) {
    $file_dir = $CONFIG_VEND . "/" . $file;
    next if ( $file eq '.' );
    next if ( $file eq '..' );

    print "Unlinking $file_dir\n";
    if ( ! unlink($file_dir) ) {
      logEvent(
        $MSG_FTMON_INTERNAL_ERROR,
        [ "unlink()", 
          __LINE__, 
          "Could not unlink '$file_dir': $!"]);
      exit(1);
    }
  }

  print "Removing $CONFIG_VEND\n";
  if ( ! rmdir($CONFIG_VEND) ) {
    logEvent(
      $MSG_FTMON_INTERNAL_ERROR,
      [ "opendir()", 
        __LINE__, 
        "Could not open '$CONFIG_VEND': $!"]);
    exit(1);
  }
}

# -----------------------------------------------------------------------
sub Help {
    print "Ha, I need more help than you :) \n";
}



# -------------------------------------------------------------------------
sub cleanChkDir {
  mkdir($CHECK_DIR) if ( ! -d $CHECK_DIR );
  if ( ! opendir(CDIR, "$CHECK_DIR") ) {
    logEvent(
      $MSG_FTMON_INTERNAL_ERROR,
      [ "opendir()", 
        __LINE__, 
        "Could not open '$CHECK_DIR': $!"]);
    exit(1);
  }


  foreach ( readdir(CDIR) ) {
    next if ( /\./ );
    next if ( /\.\./ );

    $chk_path = $CHECK_DIR . "/" . $_;
    $from_path =  $FROM_DIR . "/" . $_;
    if ( ! -e $from_path ) {
      if ( ! unlink($chk_path) ) {
        logEvent(
          $MSG_FTMON_INTERNAL_ERROR,
          [ "unlink",
            __LINE__,
            "Could not remove '$chk_path': $!"]);
        exit(1);
      }
    }
  }
  close(CDIR);
}


# -------------------------------------------------------------------------
sub logEvent {
  my $id = shift;
  my $msg = shift;
  my $type = shift;

  $type = EVENTLOG_ERROR_TYPE if ( ! defined($type) );

  my $strings = join("\0", @{$msg}); 
  my $EventLog;
  my %event=(
     'EventID',$id,
     'EventType',$type,
     'Category', NULL,
     'Strings', $strings,
     'Data',''
  );

  my $mymsg = join(" ", @{$msg}); 
  print "$mymsg \n";

  $EventLog = new Win32::EventLog('FTMON') || die $!;
  if ( ! $EventLog ) {
    print "$! \n";
  }
  if ( ! $EventLog->Report(\%event) ) {
    print "$! \n";
  }
}

package main;
