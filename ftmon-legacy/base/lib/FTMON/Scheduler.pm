package FTMON::Scheduler;
#############################################################################
#                    FTMON (Fast Track Systems Monitor)
#
#   Script: @(#) $RCSfile: Scheduler.pm,v $ 
# 
#   DESCRIPTION: 
#
#   @(#) Responsible for triggering all timed action, including monitors,
#   @(#) tasks reports etc.
#
#   $Source: /cvsroot/ftmon/base2/lib/FTMON/Scheduler.pm,v $
#
#   $Date: 2003/04/25 14:13:05 $
#
#   @(#) $Revision: 1.3 $
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
#      Sydney NSW 2122
#      AUSTRALIA
#      dsheehan@ftmon.org
#      http://ftmon.org
#
#############################################################################
require 5.002;

use TraceFuncs;
use FTMON::Base;
use FTMON::Monitor;
use FTMON::Environment;
use Devel::Peek;

{
package FT;

  BEGIN
  {
    $FT::PERSIST_DATA{'Scheduler::CYCLES'} = 0;
  };



  sub timer
  {
    $DEBUG && TraceFuncs::trace(my $f);
    my $timeout = shift;

    if ( ! $timeout )
    {
      die "FT::timer(): timeout cannot be 0 (REVISIT: Need caller subroutine)";
    }
    return( $FT::PERSIST_DATA{'Scheduler::CYCLES'} > $timeout &&
            ! ( $FT::PERSIST_DATA{'Scheduler::CYCLES'} % $timeout ) );
  }

  sub convert_date
  {
    my $fmt_str = shift;
    my $epoch_secs = shift;

    my $get_current_time = 0;
    $out_str = "";

    $fmt_str = '$dd $Month $yyyy $hh:$mm:$ss' if ( ! defined( $fmt_str ) );
    if ( ! defined( $epoch_secs ) )
    {
      $epoch_secs       = time();
      $get_current_time = 1;
    }

    local( $l_ss, $l_mm, $l_hh, $l_dd, $l_mo, $l_yy,
      $l_yyyy, $l_Day, $l_day, $l_DAY, $l_l_year,
      $l_Mth, $l_MTH, $l_Month, $l_month, $l_MONTH );

    local( $l_N_SEC,  $l_N_MIN,  $l_N_HOUR, $l_N_MDAY, $l_N_MONTH, 
      $l_N_YEAR, $l_N_WDAY, $l_N_YDAY, $l_N_ISDST );



    ( $l_N_SEC,  $l_N_MIN,  $l_N_HOUR, $l_N_MDAY, $l_l_month,
      $l_l_year, $l_N_WDAY, $l_N_YDAY, $l_N_ISDST )
             = localtime( $epoch_secs );

    $l_N_MONTH = $l_l_month + 1;

    $l_ss = sprintf("%02d", $l_N_SEC );
    $l_mm = sprintf("%02d", $l_N_MIN );
    $l_hh = sprintf("%02d", $l_N_HOUR );

    $l_dd = sprintf("%02d", $l_N_MDAY );
    $l_mo = sprintf("%02d", $l_N_MONTH );

    $l_N_YEAR = 1900 + $l_l_year;

    $l_yyyy   = sprintf("%04d", $l_N_YEAR );

    $l_l_year -= 100 if ( $l_l_year >= 100 );
    $l_yy = sprintf("%02d", $l_l_year );
  
    $l_Day = (Sun,Mon,Tue,Wed,Thr,Fri,Sat)[$l_N_WDAY];

    $l_day = $l_Day;
    $l_day =~ tr/A-Z/a-z/;

    $l_DAY = $l_Day;
    $l_DAY =~ tr/a-z/A-Z/;


    $l_Mth = (Jan, Feb, Mar, Apr, May, Jun, 
              Jul, Aug, Sep, Oct, Nov, Dec)[$l_l_month];

    $l_mth = $l_Mth;
    $l_mth =~ tr/A-Z/a-z/;

    $l_MTH = $l_Mth;
    $l_MTH =~ tr/a-z/A-Z/;


    $l_Month =
     ( January, February, March, April, May, June, July,
       August, September, October, November, December )[$l_l_month];

    $l_month = $l_Month;
    $l_month =~ tr/A-Z/a-z/;

    $l_MONTH = $l_Month;
    $l_MONTH =~ tr/a-z/A-Z/;


    #
    # Update the global "current time" variable.
    #
    if ( ! $get_current_time )
    {
      $fmt_str =~ s/\$/\$l_/g;
    }
    else
    {
      $ss = $l_ss;
      $mm = $l_mm;
      $hh = $l_hh;
      $dd = $l_dd;
      $mo = $l_mo;
      $yy = $l_yy;
      $yyyy = $l_yyyy;
      $Day = $l_Day;
      $day = $l_day;
      $DAY = $l_DAY;
      $Mth = $l_Mth;
      $MTH = $l_MTH;
      $Month = $l_Month;
      $month = $l_month;
      $MONTH = $l_MONTH;
      $N_SEC = $l_N_SEC;
      $N_MIN = $l_N_MIN;
      $N_HOUR = $l_N_HOUR;
      $N_MDAY = $l_N_MDAY;
      $N_MONTH = $l_N_MONTH;
      $N_YEAR = $l_N_YEAR;
      $N_WDAY = $l_N_WDAY;
      $N_YDAY = $l_N_YDAY;
      $N_ISDST = $l_N_ISDST;
    }

    # REVISIT:
    eval "\$out_str = \"$fmt_str\";";
    if ( $@ )
    {
      die "FT::convert_date: Invalid format string '$fmt_str'";
    }

    return( $FT::out_str );
  }

  # ----------------------------------------------------------------

  sub _convert_date
  {
    my $fmt_str = shift;
    my $epoch_secs = shift;

    package FT;
    my $get_current_time = 0;
    $out_str = "";

    $fmt_str = '$dd $Month $yyyy $hh:$mm:$ss' if ( ! defined( $fmt_str ) );
    if ( ! defined( $epoch_secs ) )
    {
      $epoch_secs       = time();
      $get_current_time = 1;
    }

    local( $l_ss, $l_mm, $l_hh, $l_dd, $l_mo, $l_yy,
      $l_yyyy, $l_Day, $l_day, $l_DAY, $l_l_year,
      $l_Mth, $l_MTH, $l_Month, $l_month, $l_MONTH );

    local( $l_N_SEC,  $l_N_MIN,  $l_N_HOUR, $l_N_MDAY, $l_N_MONTH, 
      $l_N_YEAR, $l_N_WDAY, $l_N_YDAY, $l_N_ISDST );



    ( $l_N_SEC,  $l_N_MIN,  $l_N_HOUR, $l_N_MDAY, $l_l_month,
      $l_l_year, $l_N_WDAY, $l_N_YDAY, $l_N_ISDST )
             = localtime( $epoch_secs );

    $l_N_MONTH = $l_l_month + 1;

    $l_ss = sprintf("%02d", $l_N_SEC );
    $l_mm = sprintf("%02d", $l_N_MIN );
    $l_hh = sprintf("%02d", $l_N_HOUR );

    $l_dd = sprintf("%02d", $l_N_MDAY );
    $l_mo = sprintf("%02d", $l_N_MONTH );

    $l_N_YEAR = 1900 + $l_l_year;

    $l_yyyy   = sprintf("%04d", $l_N_YEAR );

    $l_l_year -= 100 if ( $l_l_year >= 100 );
    $l_yy = sprintf("%02d", $l_l_year );
  
    $l_Day = (Sun,Mon,Tue,Wed,Thr,Fri,Sat)[$l_N_WDAY];

    $l_day = $l_Day;
    $l_day =~ tr/A-Z/a-z/;

    $l_DAY = $l_Day;
    $l_DAY =~ tr/a-z/A-Z/;


    $l_Mth = (Jan, Feb, Mar, Apr, May, Jun, 
              Jul, Aug, Sep, Oct, Nov, Dec)[$l_l_month];

    $l_mth = $l_Mth;
    $l_mth =~ tr/A-Z/a-z/;

    $l_MTH = $l_Mth;
    $l_MTH =~ tr/a-z/A-Z/;


    $l_Month =
     ( January, February, March, April, May, June, July,
       August, September, October, November, December )[$l_l_month];

    $l_month = $l_Month;
    $l_month =~ tr/A-Z/a-z/;

    $l_MONTH = $l_Month;
    $l_MONTH =~ tr/a-z/A-Z/;


    #
    # Update the global "current time" variable.
    #
    if ( ! $get_current_time )
    {
      $fmt_str =~ s/\$/\$l_/g;
    }
    else
    {
      $ss = $l_ss;
      $mm = $l_mm;
      $hh = $l_hh;
      $dd = $l_dd;
      $mo = $l_mo;
      $yy = $l_yy;
      $yyyy = $l_yyyy;
      $Day = $l_Day;
      $day = $l_day;
      $DAY = $l_DAY;
      $Mth = $l_Mth;
      $MTH = $l_MTH;
      $Month = $l_Month;
      $month = $l_month;
      $MONTH = $l_MONTH;
      $N_SEC = $l_N_SEC;
      $N_MIN = $l_N_MIN;
      $N_HOUR = $l_N_HOUR;
      $N_MDAY = $l_N_MDAY;
      $N_MONTH = $l_N_MONTH;
      $N_YEAR = $l_N_YEAR;
      $N_WDAY = $l_N_WDAY;
      $N_YDAY = $l_N_YDAY;
      $N_ISDST = $l_N_ISDST;
    }

    # REVISIT:
    eval "\$out_str = \"$fmt_str\";";
    if ( $@ )
    {
      die "FT::convert_date: Invalid format string '$fmt_str'";
    }

    package main;
    return( $FT::out_str );
  }

};

{
package FT;

  BEGIN
  { 
    FT::_convert_date();
  } 
};



  $DEBUG = 0 if ( ! defined($FTMON::Scheduler::DEBUG) ); 

  @FTMON::Scheduler::ISA = ("FTMON::Base");

  my $HTML_FILE = "jobs.html";
  
  $_LAST_ATTRIB = $FTMON::Base::_LAST_ATTRIB + 7;
  my($_JOBS,
     $_NOW_JOBS,
     $RUNNING,
     $CYCLE_PERIOD,
     $TTL,
     $TTL_COUNT,
     $CYCLES) = ( $FTMON::Base::_LAST_ATTRIB + 1 .. $_LAST_ATTRIB );

  # -------------------------------------------------------------------------
  sub new
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $proto = shift;
    my $base_interval = shift;
    my $ttl = shift;

    my $class = ref($proto) || $proto;
    
    my $self = $class->SUPER::new("Scheduler");


    # REVISIT:
    $self->[$CYCLE_PERIOD] = (defined($base_interval)) ? $base_interval : 5;
    $self->[$TTL]          = (defined($ttl)) ? $ttl : undef;
    $self->[$TTL_COUNT]    = (defined($ttl)) ? $ttl : undef;

    # private class data
    $self->[$_JOBS]  = [];
    $self->[$_NOW_JOBS]  = [];
    $self->[$CYCLES] = \$FT::PERSIST_DATA{'Scheduler::CYCLES'};
  
    bless($self, $class);

    return($self);
  
  }
  $SINGLETON = FTMON::Scheduler->new();


  # ----------------------------------------------------------------------
  sub DESTROY
  {
    my $self  = shift;
    $self->SUPER::DESTROY();
  }


  # -------------------------------------------------------------------------
  sub start
  {
    $DEBUG && TraceFuncs::trace(my $f);
    my $self = shift;
    my @due_jobs = ();
    my $is_jobs = 0;

    $self->[$RUNNING] = 1;
    my $cycle_period;
    my $start_time;
    my $time_taken;
    while ( $self->[$RUNNING] )
    {
      $cycle_period = $self->[$CYCLE_PERIOD];
      FT::_convert_date();
 
      $DEBUG && TraceFuncs::debug("cycle = " . ${ $self->[$CYCLES]} );
      @due_jobs = ();
      foreach $job ( @{$self->[$_JOBS]} )
      {
        if ($job->is_due())
        {
          push(@due_jobs, $job);
          $is_jobs = 1;
        }
      }

      foreach $job ( sort { $b->priority() <=> $a->priority() } @due_jobs )
      {
        $job->error_status($FTMON::Job::OK);

        if ( $cycle_period < 0 )
        {
          $job->error_status($FTMON::Job::MAINTENANCE);
          $job->error_message("Too many jobs scheduled - job not run.");
        }

        $start_time = time();
        eval
        {
          ($error_status, $error_msg) = &{$job->task()}($job->object());
        };
        my $status = $@;

        $time_taken = time() - $start_time;
        $cycle_period = $cycle_period - $time_taken;

        if ($status)
        {
          # REVISIT: in debug mode make sure you exit.
          $status =~ s/\n//g;
          $job->error_status($FTMON::Job::FAILED);
          $job->error_message($status);
        }
        elsif ( $error_status )
        {
          $job->error_status($error_status);
          $job->error_message($error_msg);
        }
      }

      if ( defined($self->[$TTL_COUNT]) )
      {
        if ( $self->[$TTL_COUNT] <= 0  )
        {
          $self->[$TTL_COUNT] = $self->[$TTL];
          last 
        }
        else
        {
          -- $self->[$TTL_COUNT];
        }
      }

      #
      # Generate the html page
      #
      if ( $is_jobs )
      {
        my $html_path = $FTMON::Environment::SINGLETON->html_dir() . "/"  .
                    $HTML_FILE;
        open(HTML,  "> $html_path") || 
          die "Could not open $html_path - $!";

        $self->dump(HTML);

        close(HTML) ||
          die "Could not write to $html_path - $!";
      }
      @{$self->[$_NOW_JOBS]} = ();
      

      $cycle_period = $cycle_period - $time_taken;
      sleep($cycle_period);

      ++ ${ $self->[$CYCLES]};
    }
  }


  # ----------------------------------------------------------------------
  BEGIN
  {
    my(@col_names) = ("Name", "Description", "Priority", "Status" );

    # post-condition:
    #        - open jobs sorted and dumped to html file
    sub dump
    {
      $DEBUG && TraceFuncs::trace(my $f);

      local($self, *fh) = @_;
      

      FTMON::Helper::http_page_begin(
         *fh, 
         "jobs", 
         60,
         "<P><b>Description:</b> Job Summary shows the status of jobs that have run. Jobs can be Monitors or Command Actions triggered by Monitors. If the Job Status is not OK the Description column will provide details on the problem.</P>", 
         "./");

      FTMON::Helper::http_table_start(*fh, "", \@col_names);

      my $job = undef;
      my @job_details = ();
      foreach $job ( ( @{$self->[$_JOBS]}, @{$self->[$_NOW_JOBS]} ) )
      {
        my $msg = $job->description();
        $msg = $msg . "<br>\n <b>ERROR: " . $job->error_message() . "</b>"
           if ( $job->error_status() == $FTMON::Job::FAILED );
        push(@job_details,
          [ $job->name(),
            $msg,
            $job->priority(),
            $FTMON::Job::STATUS_STR[$job->error_status()], 
          ] );

      }
      
      FTMON::Helper::print_table(*fh, \@job_details, '-1');
      FTMON::Helper::http_table_end(*fh);

      FTMON::Helper::http_page_end(*fh);
    }
  };

  # -------------------------------------------------------------------------
  sub stop
  {
    $DEBUG && TraceFuncs::trace(my $f);
    my $self = shift;
    $self->[$RUNNING] = 0;
  }


  # -------------------------------------------------------------------------
  sub cycles
  {
    my $self = shift;
    if (@_) 
    {
      $$self->[$CYCLES] = shift;
    }
    return($$self->[$CYCLES]);
  }



  # -------------------------------------------------------------------------
  sub cycle_period
  {
    my $self = shift;
    if (@_) 
    {
      $self->[$CYCLE_PERIOD] = shift;
    }
    return($self->[$CYCLE_PERIOD]);
  }


 # ------------------------------------------------------------------------
 # at
 #    schedule
 #    job
 #
 # pre-condition:
 #   - function is not already scheduled
 #
 # post-condition
 #   - monitor invoked every 'interval' starting at 'day_offset'
 #
 sub at
 {
   $DEBUG && TraceFuncs::trace(my $f);

   my $self = shift;
   my $job = shift;

   $DEBUG && TraceFuncs::debug( "Job = " . ($job->name()) );

   #
   # Check if job already exists & replace it
   #
   my $old_job;
   foreach $old_job (  @{$self->[$_JOBS]} )
   {
     if ( $old_job->name() eq $job->name() )
     {
       $old_job = $job;
       return;
     }
   }

   # completely new job.
   push(@{$self->[$_JOBS]}, $job);
 }

 # ------------------------------------------------------------------------
 # now
 #    job
 #
 # pre-condition:
 #   - function is not already scheduled
 #
 # post-condition
 #   - job invoked now.
 #
 sub now
 {
   $DEBUG && TraceFuncs::trace(my $f);
   my $self = shift;
   my $job = shift;


   $DEBUG && TraceFuncs::debug( "Job = " . ($job->name()) );

   my $error_status = $FTMON::Job::OK;
   my $error_msg = "";

   eval
   {
     ($error_status, $error_msg) = &{$job->task()}();
   };

   my $status = $@;
   if ($status)
   {
     # REVISIT: in debug mode make sure you exit.
     $status =~ s/\n//g;
     $job->error_status($FTMON::Job::FAILED);
     $job->error_message($status);
   }
   elsif ( $error_status )
   {
     $job->error_status($error_status);
     $job->error_message($error_msg);
   }

   push(@{$self->[$_NOW_JOBS]}, $job);
 }


 # ------------------------------------------------------------------------
 sub start_maintenance
 {
 }

 # ------------------------------------------------------------------------
 sub stop_maintenance
 {
 }

 # ------------------------------------------------------------------------
 sub set_public_holiday
 {
 }


package FTMON::Job;

  $DEBUG = 0 if ( ! defined($FTMON::Job::DEBUG) ); 

  @FTMON::Job::ISA = ("FTMON::Base");
  
  
  ( $OK,
    $FAILED,
    $MAINTENANCE ) = ( 0 .. 2 );

  @STATUS_STR =
  (
    "<FONT COLOR=\"Green\">OK</FONT>",
    "<FONT COLOR=\"Red\">FAILED</FONT>",
    "<FONT COLOR=\"Yellow\">MAINTENANCE</FONT>",
  );

  $_LAST_ATTRIB = $FTMON::Base::_LAST_ATTRIB + 9;
  my($NAME,
     $SCHEDULE,
     $OBJECT,
     $TASK,
     $HOSTNAME,
     $DESCRIPTION,
     $PRIORITY,
     $ERROR_STATUS,
     $ERROR_MSG,) = 
       ( $FTMON::Base::_LAST_ATTRIB + 1 .. $_LAST_ATTRIB );


  # -------------------------------------------------------------------------
  sub new
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $proto = shift;
    my $name = shift;
    my $schedule = shift;
    my $task = shift;
    my $hostname = shift;
    my $description = shift;
    my $priority = shift;
    my $object = shift;

    die "You must define a schedule"    if ( ! defined($SCHEDULE) );
    die "You must define a task to run" if ( ! defined($TASK) );

    my $class = ref($proto) || $proto;
    #if ( $FTMON::Base::SINGLETON->find_instance($class, $name) )
    #{
    #  die "scheduled job $name already exists."
    #}

    $DEBUG && TraceFuncs::debug("Creating a new instance");
    $self = $class->SUPER::new($name);
    bless($self, $class);

    $self->[$SCHEDULE] = $schedule;
    $self->[$TASK]     = $task;

    $self->[$NAME]        = (defined($name)) ? $name : "n/a";
    $self->[$DESCRIPTION] = (defined($description)) ? $description : "n/a";
    $self->[$HOSTNAME]    = (defined($hostane)) ? $hostname : $FT::HOSTNAME;

    $self->[$ERROR_STATUS] = $FTMON::Job::OK;
    $self->[$ERROR_MSG] = "";
    $self->[$PRIORITY] = (defined($priority)) ? $priority : 10;
    $self->[$OBJECT] = $object;

    return($self);
  }


  # ----------------------------------------------------------------------
  sub DESTROY
  {
    my $self  = shift;
    $self->SUPER::DESTROY();
  }


  # -------------------------------------------------------------------------
  sub schedule
  {
    my $self = shift;
    if (@_) 
    {
      $self->[$SCHEDULE] = shift;
    }
    return($self->[$SCHEDULE]);
  }

  # -------------------------------------------------------------------------
  sub name
  {
    my $self = shift;
    if (@_) 
    {
      $self->[$NAME] = shift;
    }
    return($self->[$NAME]);
  }

  # -------------------------------------------------------------------------
  sub priority
  {
    my $self = shift;
    if (@_) 
    {
      $self->[$PRIORITY] = shift;
    }
    return($self->[$PRIORITY]);
  }


  # -------------------------------------------------------------------------
  sub description
  {
    my $self = shift;
    if (@_) 
    {
      $self->[$DESCRIPTION] = shift;
    }
    return($self->[$DESCRIPTION]);
  }

  # -------------------------------------------------------------------------
  sub object
  {
    my $self = shift;
    if (@_) 
    {
      $self->[$OBJECT] = shift;
    }
    return($self->[$OBJECT]);
  }


  # -------------------------------------------------------------------------
  sub is_due
  {
    my $self = shift;
    return(&{$self->[$SCHEDULE]}());
  }


  # -------------------------------------------------------------------------
  sub error_status
  {
    my $self = shift;
    if (@_) 
    {
      $self->[$ERROR_STATUS] = shift;
    }
    return($self->[$ERROR_STATUS]);
  }

  # -------------------------------------------------------------------------
  sub error_message
  {
    my $self = shift;
    if (@_) 
    {
      $self->[$ERROR_MSG] = shift;
    }
    return($self->[$ERROR_MSG]);
  }



  # -------------------------------------------------------------------------
  sub task
  {
    my $self = shift;
    if (@_) 
    {
      $self->[$TASK] = shift;
    }
    return($self->[$TASK]);
  }

1;


__END__;

######################## User Documentation ##########################

## To format the following documentation into a more readable format,
## use one of these programs: perldoc; pod2man; pod2html; pod2text.
## For example, to nicely format this documentation for printing, you
## may use pod2man and groff to convert to postscript:
##   pod2man Net/Telnet.pm | groff -man -Tps > Net::Telnet.ps

=head1 NAME

FTMON::Scheduler - 

=head1 SYNOPSIS

See METHODS section below

=head1 DESCRIPTION

FTMON::Base

=head1 METHODS

=head1 SEE ALSO

=head1 EXAMPLES

=head1 AUTHOR

Danny Sheehan <dsheehan@ftmon.org>

=cut
