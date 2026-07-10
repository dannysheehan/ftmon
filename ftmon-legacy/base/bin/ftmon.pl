#!/usr/bin/perl
#############################################################################
#                    FTMON (Fast Track Systems Monitor)
#
#   Script: @(#) $RCSfile: ftmon.pl,v $ 
# 
#   DESCRIPTION: 
#
#   @(#) FTMON is a free extensable systems monitor that can be
#   @(#) integrated to forward events to a number of free and commercial
#   @(#) event management systems.
#
#   $Source: /cvsroot/ftmon/base2/bin/ftmon.pl,v $
#
#   $Date: 2003/04/27 10:23:25 $
#
#   @(#) $Revision: 1.5 $
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
$FT::VERSION = '@(#) $Revision: 1.5 $';
$FT::VERSION =~ s/[^\d.]//g;

$FT::CYCLE_PERIOD = 1;
$FT::CHECK_CONFIG_INTERVAL = 2;

use Getopt::Std;

BEGIN {
  umask(022);

  if ( ! exists($ENV{'BASE_DIR'}) )
  {
    die "You must define the BASE_DIR";
  }
  $FT::BASE_DIR = $ENV{'BASE_DIR'};
  $FT::BASE_DIR =~ s/\\/\//g;

  if ( ! -d $FT::BASE_DIR )
  {
    die "$FT::BASE_DIR must exist as a directory.";
  }



  push( @INC, "$FT::BASE_DIR/lib" ); 
  push( @INC, "$FT::BASE_DIR/lib/$^O" );
  push( @INC, "./" );
};  

use FTMON::ConfigFile;
use FTMON::ConfigFileParser;
use FTMON::Scheduler;
use FTMON::Monitor;
#use FTMON::CheckRRD;
use FTMON::RRD;
use FTMON::SNMP;
use FTMON::LogFileScraper;
#use FTMON::EventManager::LogFile;
use NET::Telnet;
use Digest::MD5;

load_opts();

exit(0);



# ----------------------------------------------------------------------------
  BEGIN {
   my $OPTIONS =  'htre:w:o:a:c:d:z:i:f:v:p:l:g:';

   my $SCRIPT = $0;
   my $USAGE = <<EOF;

   $SCRIPT -h
   $SCRIPT [-t] [-p base_dir] [-v interval] [-i config] [ -f outputfile ] [ -g pidfile ]
   $SCRIPT [ -p base_dir ] -i config -z info_type
   $SCRIPT -c config_file_path
   $SCRIPT -e string [-w password]

   -i<instance> = sets name of monitor if running multiple instances
        of $SCRIPT.

   -f<outputfile> = sets name of file to direct stdout and stderr output to

   -a = base directory

   -o = web directory

   -p = config dir = forces $SCRIPT to use the specified base cfg directory.

   -l = log dir = forces $SCRIPT to use the specified log directory

   -h = prints out this message.

   -d = debug mode. Detailed tracing is logged to STDOUT if run
         from the command line. If run from a monitor tracing is
         logged to $SCRIPT.log.

   -c config_file = check the <config_file> file for errors, errors are logged
        to STDERR. <config_file> must be be the full path and name of the
	config file.

   -t = runs the monitor in test mode. Each iteration it will output a
       "definable" test line, reflecting the status of the monitor.

   -u = Remove (unlocks) the $SCRIPT lock file and clean up
        log & data files created by $SCRIPT.

   -h = prints out this message.


   -z type = prints lots of information about this monitor.
           -z 1 - prints out a list of the current problems detected.
           -z 2 - prints out report on number of problems detected and
	       time it took monior to run.

   -d = debug mode. Detailed tracing is logged to STDOUT if run
         from the command line. If run as a monitor then REVISIT:

   -v interval = tells $SCRIPT ito self shedule itself every <interval> 
       seconds.

   -w password  = specifies password to use to encrypt <string>
 
   -e string  = specifies string to be encrypted. STDOUT will be the
       encrypted string.

  NOTE
  1. $SCRIPT will not work if it can find the base directory or if you
  don't speciy one ( with -p option).
EOF

  # -------------------------------------------------------------------------
  sub load_opts
  {
    my %opt = ();
    # @FT::ARGS =  @ARGV;

    if ( ! getopts($OPTIONS, \%opt) )
    {
      print STDERR $USAGE;
      exit(1);
    }

    my $l_password = $FT::HOSTNAME;
    if ( $opt{'w'} )
    {
      $l_password = $opt{'w'};
    }
    
    if ( $opt{'f'} )
    {
      my $output_file = $opt{'f'};

      open(SAVEOUT, '>&STDOUT');
      open(SAVEERR, '>&STDERR');

      open(STDOUT, "> $output_file") || die "Could not open '$output_file': $!";
      open(STDERR, '>&STDOUT');
    }

    if ( $opt{'h'} )
    {
      print STDERR $USAGE;
      exit(0);
    }
    elsif ( $opt{'e'} )
    {
      my $encrypted_str = FT::encrypt($l_password, $opt{'e'});
      print $encrypted_str;
      exit(0);
    }

    $FT::BASE_DIR = $ENV{BASE_DIR} if defined $ENV{BASE_DIR};
    if ( $opt{'a'} )
    {
      $FT::BASE_DIR = $opt{'a'};
    }
    $FT::BASE_DIR =~ s/\\/\//g;

    $FT::HTML_DIR = $FT::BASE_DIR . "/html";
    if ( $opt{'o'} )
    {
      $FT::HTML_DIR = $opt{'o'};
      $FT::HTML_DIR =~ s/\\/\//g;
    }

    $FT::PID_FILE = $FT::LOG_DIR . "/ftmon.pid";
    if ( $opt{'g'} )
    {
      $FT::PID_FILE = $opt{'g'};
      $FT::PID_FILE =~ s/\\/\//g;
    }


    if ( $FT::HTML_DIR && ! -d $FT::HTML_DIR )
    {
      mkdir($FT::HTML_DIR, 0755) || 
         die "Could not make $FT::HTML_DIR directory - $!";
    }
    
    FTMON::Helper::file_copy("$FT::BASE_DIR/ftmon2_small.jpg", 
                      "$FT::HTML_DIR/ftmon2_small.jpg");

    FTMON::Helper::file_copy("$FT::BASE_DIR/rrdtool.gif", 
                      "$FT::HTML_DIR/rrdtool.gif");

    FTMON::Helper::file_copy("$FT::BASE_DIR/help.html", 
                      "$FT::HTML_DIR/help.html");



    $FT::CFG_DIR = $FT::BASE_DIR . "/cfg";
    if ( $opt{'p'} )
    {
      $FT::CFG_DIR = $opt{'p'};
      $FT::CFG_DIR =~ s/\\/\//g;
    }

    $FT::LOG_DIR = $FT::BASE_DIR . "/logs";
    if ( $opt{'l'} )
    {
      $FT::LOG_DIR = $opt{'l'};
      $FT::LOG_DIR =~ s/\\/\//g;
    }
    

    if ( $FT::LOG_DIR && ! -d $FT::LOG_DIR )
    {
      mkdir($FT::LOG_DIR, 0755) || 
         die "Could not make $FT::LOG_DIR directory - $!";
    }
    TraceFuncs::trace_file($FT::LOG_DIR . "/ftmon.log");


    my $env_file = $FT::CFG_DIR . "/ftmon.cfg";
    if ( ! -f "$env_file" )
    {
      print STDERR $USAGE;
      die "You must defined '$env_file'";
    }

    eval
    {
     require "$env_file";
    };

    my $status = $@;
    if ( $status )
    {
      $status =~ s/\n//g;
      die "ERROR: $status";
    } 

    my $event_manager_file = $FT::CFG_DIR . "/event_manager.cfg";
    if ( ! -f "$event_manager_file" )
    {
      die "You must define '$event_manager_file'";
    }
  
    eval
    {
     require "$event_manager_file";
    };
    $status = $@;
    if ( $status )
    {
      $status =~ s/\n//g;
      die "ERROR: $status";
    }

    my $html_path = $FT::HTML_DIR . "/event_manager.html";
    open(EM_FH, "> $html_path") ||
       die "Could not open '$html_path' - $!";
    $FT::EVENT_MGR->dump_html(*EM_FH);
    close(EM_FH);


    #if ( $^O eq "MSWin32" )
    #{
    #  eval
    #{
    #require "FTMON/NT.pm";
    #require "FTMON/EventLogScraper.pm";
    #};
    #}


    if ( $opt{'i'}  && $opt{'z'} )
    {
      print "REVISIT: get info\n";
      exit(0);
    }


    elsif ( $opt{'i'} )
    {
      my $status = run_monitor($opt{'i'}, $opt{'v'});
      exit($status);
    }

    elsif ( ! $opt{'i'} && $opt{'v'} )
    {
      my $status = run_all_monitor($opt{'v'});
      exit($status);
    }



    elsif ( $opt{'c'} )
    {
      print "REVISIT: check config file\n";
      exit(0);
    }
    else
    {
      my $status = run_all_monitor();
      exit($status);
    }
  };

};


# --------------------------------------------------------------------------
sub run_monitor
{
  my $monitor      = shift;
  my $cycle_period = shift;

  my $config_file = FTMON::ConfigFile->new($monitor);
  $config_file->compile();
}

sub kick_off
{
  #

  my $cycle_period = shift;
  if (! defined($cycle_period) ) 
  {
    ;# REVISIT:
  }
  else
  {
    $FTMON::Scheduler::SINGLETON->cycle_period($cycle_period);

    sub timer_sub { 1 };
    sub send_events_sub { $FT::EVENT_MGR->send_events() };
    my $send_events_job      = 
       FTMON::Job->new(
	 "EVENTS: <a href=\"events.html\">Event Summary</a>",
         \&timer_sub,
         \&send_events_sub,
	 $FT::HOSTNAME,
	 "Events forwarded to '" . $FT::EVENT_MGR->name() . "' EventManager.",
	 0);
    $FTMON::Scheduler::SINGLETON->at($send_events_job);

    my $check_config_file      = 
       FTMON::Job->new(
	 "MAINT: Check for config file changes.",
         sub { FT::timer($FT::CHECK_CONFIG_INTERVAL) },
         sub { reload_config() },
	 $FT::HOSTNAME,
	 "Re-compile configuration files that have changed",
	 0);
    $FTMON::Scheduler::SINGLETON->at($check_config_file);

#     my $clean_up_rrd      = 
#        FTMON::Job->new(
# 	 "MAINT: Job for cleaning up RRDs.",
#          sub { FT::timer(2) },
#          sub { clean_rrds() },
# 	 $FT::HOSTNAME,
# 	 "Cleanup RRDs that are no longer used",
# 	 0);
#     $FTMON::Scheduler::SINGLETON->at($clean_up_rrd);


    $FT::FTMON_LOG_RECYCLE = 50 if ( ! defined $FT::FTMON_LOG_RECYCLE );

    my $recycle_ftmon_log      = 
       FTMON::Job->new(
	      "MAINT: Clear out FTMON LOG.",
         sub { FT::timer($FT::FTMON_LOG_RECYCLE) },
         sub { TraceFuncs::trace_file()->close(); 
               TraceFuncs::trace_file($FT::LOG_DIR . "/ftmon.log"); },
	 $FT::HOSTNAME,
	 "Truncate and re-open ftmon.log",
	 0);
    $FTMON::Scheduler::SINGLETON->at($recycle_ftmon_log);


    if ( defined $FT::REEXEC && $FT::REEXEC )
    {
      my $reexec_ftmon      = 
         FTMON::Job->new(
  	 "REXEC: Recycle the FTMON daemon.",
           sub { FT::timer($FT::REEXEC) },
           sub { reexec() },
	 $FT::HOSTNAME,
	 "Re-exec ftmon daemon every $FT::REEXEC cycles.",
	 0);
      $FTMON::Scheduler::SINGLETON->at($reexec_ftmon);
    }

    my $list_products_job      = 
       FTMON::Job->new(
	 "PRODUCTS: <a href=\"index.html\">Product Summary</a>",
         sub { 1 },
         sub { $FTMON::Environment::SINGLETON->dump_html() },
	 $FT::HOSTNAME,
	 "List of Applications currently being monitored by FTMON.",
	 0);
    $FTMON::Scheduler::SINGLETON->at($list_products_job);

    $flite = $FT::BASE_DIR . "/lib/" . $^O . "/flite_time";
    if ( ! -f "$flite" )
    {
      my $say_time      = 
         FTMON::Job->new(
  	 "REXEC: Say the time.",
           sub { $FT::mm == 0 || $FT::mm == 15 || $FT::mm == 30 || 
                 $FT::mm == 45  },
           sub { saytime() },
	 $FT::HOSTNAME,
	 "Says the time every 15 minutes.",
	 0);
      $FTMON::Scheduler::SINGLETON->at($say_time);
    }

    $FTMON::Scheduler::SINGLETON->start();
  }
}

# --------------------------------------------------------------------------
sub saytime
{
    my $flite = $FT::BASE_DIR . "/lib/" . $^O . "/flite_time";
    system($flite, $FT::hh . ":" . $FT::mm);
}

# --------------------------------------------------------------------------
sub run_all_monitor
{
  my $cycle_period = shift;

  #$SIG{'PIPE'} = 'IGNORE'; # ignore Broken Pipe

  $SIG{'CHILD'} = 'IGNORE'; # stop zombies (on some systems).
  $|++;

  # Change working directory
  chdir("/");

  # Clear file creation mask
  umask(0);

  # Close open file descriptors
  close(STDIN);
  close(STDOUT);
  close(STDERR);

  fork() && exit(0);


  open(PID_H, "> $FT::PID_FILE") ||
      die "Could not open '$FT::PID_FILE': $!";
  print PID_H $$, "\n";
  close(PID_H);


  $FT::CYCLE_PERIOD = $cycle_period;

  my $cfg_dir = $FTMON::Environment::SINGLETON->cfg_dir();
  opendir(VENDOR, $cfg_dir) || die "Could not open '$cfg_dir' : $!";

  foreach $vendor ( readdir(VENDOR) )
  {
    my $vendor_dir = $cfg_dir . "/" . $vendor;
    next if ( $vendor eq '.' );
    next if ( $vendor eq '..' );
    next if ( ! -d $vendor_dir );

    opendir(PRODUCT, $vendor_dir) || die "Could not open '$vendor_dir' : $!";
    foreach $product ( readdir(PRODUCT) )
    {
      my $product_dir = $vendor_dir . "/" . $product;
      next if ( $product eq '.' );
      next if ( $product eq '..' );
      next if ( ! -d $product_dir );

      opendir(MONITOR, $product_dir) || 
           die "Could not open '$product_dir' : $!";
      foreach $monitor ( readdir(MONITOR) )
      {
        my $monitor_cfg = $product_dir . "/" . $monitor;
	next if ( $monitor !~ /\.cfg$/ );
	next if ( $monitor eq 'common.cfg' );
        next if ( ! -f $monitor_cfg );
	chdir($product_dir) ||
	    die "ftmon: could not change directory to $product_dir: $!";
	
	print "RUN_MONITOR - $product_dir ( $monitor_cfg ) \n"; 
	run_monitor($monitor_cfg, $cycle_period); 
      }
      closedir(MONITOR);
    }
    closedir(PRODUCT);
  }
  closedir(VENDOR);

  kick_off($cycle_period);
}

# --------------------------------------------------------------------------
#sub clean_rrds
#{
#  FTMON::RRD::collect_garbage();
#}



# --------------------------------------------------------------------------
sub reload_config
{
  # Convert to days.


  my $event_mgr = $FT::EVENT_MGR;
  my $cfg_dir = $FTMON::Environment::SINGLETON->cfg_dir();

  opendir(VENDOR, $cfg_dir) || die "Could not open '$cfg_dir' : $!";

  foreach $vendor ( readdir(VENDOR) )
  {
    my $vendor_dir = $cfg_dir . "/" . $vendor;
    next if ( $vendor eq '.' );
    next if ( $vendor eq '..' );
    next if ( ! -d $vendor_dir );

    opendir(PRODUCT, $vendor_dir) || die "Could not open '$vendor_dir' : $!";
    foreach $product ( readdir(PRODUCT) )
    {
      my $product_dir = $vendor_dir . "/" . $product;
      next if ( $product eq '.' );
      next if ( $product eq '..' );
      next if ( ! -d $product_dir );

      opendir(MONITOR, $product_dir) || 
           die "Could not open '$product_dir' : $!";
      foreach $monitor ( readdir(MONITOR) )
      {
        my $monitor_cfg = $product_dir . "/" . $monitor;
	next if ( $monitor !~ /\.cfg$/ );
	next if ( $monitor eq 'common.cfg' );

        next if ( ! -f $monitor_cfg );

	my $config_file = 
	     $FTMON::ConfigFileParser::SINGLETON->find_config_file_instance(
	       $monitor_cfg);

	die "'$monitor_cfg' does not exist" if ( ! defined $config_file );

	open(CFG, $monitor_cfg ) ||
          die "Could not open file $monitor_cfg - $!";
        binmode(CFG);
        my $md5 = Digest::MD5->new->addfile(*CFG)->hexdigest();
        close(CFG);

	next if ( $md5 eq $config_file->md5() );

        $config_file->compile();
	$config_file->backup();
	#$config_file->dump_to_file($config_file->name());
      }
      closedir(MONITOR);
    }
    closedir(PRODUCT);
  }
  closedir(VENDOR);

  return $FTMON::Job::OK;
}

# --------------------------------------------------------------------------
sub reexec
{
  #
  # Shutdown alltogether and wait for my watcher to re-start me.
  # perl exec does not work on some systems as expected.
  #
  exit(0);
}

