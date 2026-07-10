package FTMON::Calculation;
#############################################################################
#                    FTMON (Fast Track Systems Monitor)
#
#   Script: @(#) $RCSfile: Calculation.pm,v $ 
# 
#   DESCRIPTION: 
#
#   @(#) The calculation module performs system managment type functions 
#   @(#) such as averaging, leak detection etc.
#
#   $Source: /cvsroot/ftmon/base2/lib/FTMON/Calculation.pm,v $
#
#   $Date: 2003/04/05 03:52:13 $
#
#   @(#) $Revision: 1.2 $
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
#      PO Box 238
#      Eastwood NSW 2122
#      AUSTRALIA
#      dsheehan@ftmon.org
#      http://ftmon.org
#
#############################################################################
require 5.002;

use FTMON::Base;
use FTMON::CalculationManager;
use FTMON::Scheduler;

use TraceFuncs;
use Socket;
use Crypt::CipherSaber;

#
# The following environmental variables are used by the FTMON calculations
#   FT::HOSTNAME
#      - Indicates the hostname the current monitor is monitoring.
#   FT::RESOURCE
#      - The resource identifier of the currently active resource.
#   FT::MONITOR::NAME
#      - The name of the currently active monitor.
#   FT::MONITOR::BASELINED
#      - The time that the monitor thresholds were last baselined.
#      Baselining is where thresholds are automatically set based
#      on past "normal" monitor value history. If a value should
#      go outside of the "baseline" then this is considered "not normal".
# 
# FT
# ==
# cmd()
#   Runs specified CLI and returns the values in an array were the rows
#   correspond to the rows in the CLI output and the columns to the 
#   columns of the CLI output.
#   
#   There are a set of filters that can be set for extracting monitor
#   values from the CLI output. 
#   
#   It is meant for people not completely comfortable with programming but
#   who at least know regular expressions.
#
#   @FT::VALUES
#   FT::CMD::PREFILTER
#   FT::CMD::HEADER_FILTER
#   FT::CMD::ERROR_FILTER
#   FT::CMD::COL_SEP
#   FT::CMD::MAX_ERROR_LINES
#   FT::CMD::HEADER_END
# 
#   FT::LINE
#     ENV{FT_LINE}
#   FT::ROW
#     ENV{FT_ROW}
# 
#   FT::ERROR_MSG
#   FT::ERROR_STATUS
# 
# match(file_name, regexp)
#   For specified 'file_name' runs the specified 'regexp' accross the
#   file contents. It records the number of times the 'regexp' matched
#   in $FT::MATCH_COUNT, the last matching line $FT::MATCH_LINE as well
#   as up to 4 matching strings if (.*) substring regexp matching is used,
#   these are returned in $FT::MATCH_1, $FT::MATCH_2, $FT::MATCH3 and
#   $FT::MATCH4.
#   The function returns zero (0) if no matches were found and the 
#   the number of matching lines (FT::MATCH_COUNT) if there are matching
#   lines.
#
#   FT::MATCH_COUNT
#     - number of lines matching 'regexp'
#   FT::MATCH_LINE
#     - the last matching line
#   FT::MATCH_1
#     - $1 from 'regexp'
#   FT::MATCH_2
#     - $2 from 'regexp'
#   FT::MATCH_3
#     - $3 from 'regexp'
#   FT::MATCH_4
#     - $4 from 'regexp'
#
# avg(samples, interval, value, is_running_avg)
#   For the currently active resource avg() will return the
#   average value over the specified number of 'samples' from the
#   the values passed ('value') during that interval. The average determined
#   from the previous monitoring interval is returned when calculating
#   the average for the current monitoring interval. 
#   'samples' are taken every 'interval' iterations of the associated monitor. 
#   'is_running_avg' by default is set false (0). If set true (1) then the
#   previous monitoring intervals average is used as a value at the start of
#   the next monitoring interval.
#
# min(samples, interval, value)
#   For the currently active resource min() will return the
#   minimun value over the specified number of 'samples" from the
#   values passed ('value') during that interval. The minimum determined
#   from the previous monitoring interval is returned when calculating
#   the minimum for the current monitoring interval.
#   'samples' are taken every 'interval' iterations of the associated monitor. 
#   
# max(samples, interval, value)
#
# delta(samples, interval, value)
#
# monot(samples, interval, value)
#
# round(value, decimal_places)
# round()
#   Rounds the specified floating point 'value' down to the specified 
#   number of 'decimal_places'.
#   The default 'decimal_places' is 2.
#
# addr2host(ip_address)
#   Convert IP address into a hostname. The return value is the hostname.
#
# m(value, regex)
#   Wraps perl regular expression matching.
#
# is_trading(non_trading_dates, trading_days, trading_start, trading_end)
# is_trading()
#   Checks if Product is in trading mode or not based on the current time.
#   Returns True (1) if the Product is trading, otherwise False (0) is
#   returned.
#
#   When used in the Monitor schedule function this function can be used
#   to prevent monitors being run when the associated product is not in
#   a trading time period e.g. Mon to Fri between 9:00am and 5:00pm.
#
#   non_trading_dates
#       Hash of strings (identifying significance of day e.g. New Years Day)
#       indexed by the date (in dd:mo::yyyy format).
#       DEFAULT: FT::NON_TRADING_DATES
#         e.g. $FT::NON_TRADING_DATES{"01:01:2000"} = "New Years Day";
#   trading_days
#       The days of the week that the product is in trading mode in
#       Mon, Tue, Wed, Thr, Fri, Sat or Sun format.
#       DEFAULT: FT::TRADING_DAYS
#         e.g. $FT::TRADING_DAYS = ["Mon", "Tue", "Wed", "Thr", "Fri"];
#   trading_start
#       Time in 24hr format that trading starts (e.g. 09:00)
#       DEFAULT: FT::TRADING_START
#          e.g. $FT::TRADING_START = "09:00"
#   trading_end
#       Time in 24hr format that trading ends (e.g. 17:00)
#       DEFAULT: FT::TRADING_END
#          e.g. $FT::TRADING_END = "09:00"
#
#   FT::TRADING_MSG{FT::VENDOR . "::" . FT::PRODUCT}
#       This global variable is set to indicate why the Product is in
#       trading mode and is displayed on the product summary HTML page.
#
# str2secs(time_str)
#   Converts time string ('time_str') into seconds. 
#   The time_str is in 'uptime' or similar format.
#   e.g. 55 min
#        5 hr
#        22:23:45 
#
# days2str(decimaldays)
#   Converts  'decimaldays' into a string equvalent and returns the string.
#   e.g. 1.5 would return as 1 day 12 hours.
#   This function is useful with perl as the -M (modification time) operator
#   and other operators returns the modification time in decimal days.
#
# encrypt(password, string)
#   Encrypts the specified 'string' using the specified 'password' and
#   returns the encrypted string.
#
# decrypt(string, password)
#   Decrypts the specified 'string' using the specified 'password' and 
#   returns the decrypted sring.
#   If no 'password' is passed then the $FT::HOSTNAME global variable is
#   used as the 'password'.
#
# decrypt_file(string, password_file)
#   Same as decrypt() function except that the password is read from a
#   password file ('password_file').
#   from certain users.
# 
# ordered_sev(severity)
#   Returns the severity level (number between 0 - 10) for the given
#   severity string. This will change depending on the particular
#   EventManger currently in use.
#
#   %FT::SEV::ORDERED
#      Global Variable as defined by the currently active EventManager.
# 
# system_retry(command, retries)
#   Retries the specified 'command' a specified number of times ('retries').
#   If the 'command' returns a non zero exit value the 'command' is 
#   re-run.
#
# parse_csv(text)
#   Parses the secified comma separated fields and returns them in an
#   array. It takes quotes ("") and back slashes (\) into account.
#   i.e. the string
#     "a,b", \,\, ,c   
#   would be parsed as
#     a,b
#     ,,
#     c
#
# FT::BL
# ======
# min(num_samples, interval, name)
# max(num_samples, interval, name)
#  The only baselining functions currently implemented are
#    FT::BL::min() & 
#    FT::BL::max()
#  They are similar to the normal FT::min & FT::max counterparts
#  except they take the glob name of the varable to be baselined i.e.
#  you would pass *AvailMB_V rather than $AvailMV_V (the actual value).
#      $AvailMB_BL_V = FT::BL::min(10, 1, *AvailMB_V);
#
# ========================================================================
$DEBUG = 0 if ( ! defined($FTMON::Calculation::DEBUG) );

@FTMON::Calculation::ISA = ("FTMON::Base");

my $Count = 0;
my %Calculations = ();

$_LAST_ATTRIB = $FTMON::Base::_LAST_ATTRIB + 4;
my(
   # Used to determine when garbage collection of this resource calculation
   # should be performed (See Calculation manager). 
   # Basically if the calculation for a given resource is not touched for
   # the scheduled interval, then the resource is no longer available and
   # we can delete the calculation.
   $TOUCHED,

   # The current value of the resource being processed.
   $DATA_VALUE,

   # A unique identifier for this calculation.
   $CALC_ID,

   # Number of samples collected so far for the calculation.
   $DATA_COUNT,
  ) = ( $FTMON::Base::_LAST_ATTRIB + 1 .. $_LAST_ATTRIB );


# -------------------------------------------------------------------------
sub new
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $proto = shift;
  my $id = shift;

  my $class = ref($proto) || $proto;
  my $self = $class->SUPER::new($id);


  $self->[$CALC_ID] = $id;
  $self->touched(1);
  $self->calc_id($id);
  bless($self, $class);

  return($self);
}

# ----------------------------------------------------------------------
sub DESTROY
{
  my $self  = shift;

  $self->SUPER::DESTROY();
}

# ----------------------------------------------------------------------
# REVISIT: Provide persistence. Not implemented yet.
# ----------------------------------------------------------------------
sub freeze
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;

  my $freeze_str = ref($self) . "->new(";

  foreach( @{$self}[$FTMON::Calculation::_LAST_ATTRIB .. @{$self}] )
  {
    $freeze_str = $freeze_str . $_ . ",";
  }
  $freeze_str =~ s/\,+$//;
  $freeze_str = $freeze_str . ");\n";

  $DEBUG && TraceFuncs::debug("freeze_str = " . $freeze_str);
  return($freeze_str);
}

# ----------------------------------------------------------------------
sub calc_id
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  
  return($self->[$CALC_ID]);
}


# ----------------------------------------------------------------------
sub touched
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  
  if ( @_ )
  {
    $self->[$TOUCHED] = shift;
  }

  $DEBUG && TraceFuncs::debug($TOUCHED . ": touched = ", $self->[$TOUCHED] );
  return($self->[$TOUCHED]);
}


# =====================================================================
package FTMON::AvgCalculation;

  @FTMON::AvgCalculation::ISA = ("FTMON::Calculation");

  $DEBUG = 0 if ( ! defined($FTMON::AvgCalculation::DEBUG) );

  $_LAST_ATTRIB = $FTMON::Calculation::_LAST_ATTRIB + 5;
  my( $DATA_END,
      $DATA_TOTAL,
      $DATA_AVG,
      $DATA_MIN,
      $DATA_MAX,) = 
         ( $FTMON::Calculation::_LAST_ATTRIB + 1 .. $_LAST_ATTRIB );

  # -------------------------------------------------------------------------
  # 
  sub count
  {
    my $self = shift;

    $DEBUG && TraceFuncs::trace(my $f);
    $DEBUG && TraceFuncs::debug("count = " . $self->[$DATA_COUNT] );
    return $self->[$DATA_COUNT];
  }

  # -------------------------------------------------------------------------
  # 
  sub end
  {
    my $self = shift;

    $DEBUG && TraceFuncs::trace(my $f);
    $DEBUG && TraceFuncs::debug("end = " . $self->[$DATA_END] );
    return $self->[$DATA_END];
  }



  # -------------------------------------------------------------------------
  #
  sub new
  {
    $DEBUG && TraceFuncs::trace(my $f);
    my $proto = shift;

    my $class = ref($proto) || $proto;

  my $id = $FTMON::CalculationManager::SINGLETON->next_calc_id();

  if ( ! ( $self = $FTMON::Base::SINGLETON->find_instance($class, $id )) )
  {
    $DEBUG && TraceFuncs::debug("New calculation - $id");
    $self = $class->SUPER::new($id);
    bless($self, $class);
  }
  else
  {
    $DEBUG && TraceFuncs::debug("Used existing calculation - $id");
    return($self);
  }
    
    $self->[$DATA_COUNT] =  0;
    $self->[$DATA_INTERVAL] =  0;
    $self->[$DATA_VALUE] =  0;
    $self->[$DATA_TOTAL] =  0;
    $self->[$DATA_AVG]   =  0;
    $self->[$DATA_MIN]   =  0;
    $self->[$DATA_MAX]   =  0;

    return($self);
  }

  # ----------------------------------------------------------------------
  sub DESTROY
  {
    my $self  = shift;
    $self->SUPER::DESTROY();
  }

  # -------------------------------------------------------------------------
  sub avg
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self           = shift;
    my $num_samples    = shift;
    my $interval       = shift;
    my $value          = shift;
    my $is_running_avg = shift;
    my $resource       = shift;

    my $key;
    my $last_count = 0;

    # my( $l_value_str )  = ( defined( $l_value ) ) ? "$l_value" : "undef";


    $resource       = $FT::RESOURCE if ( ! defined( $resource ) );
    $is_running_avg = 0             if ( ! defined( $is_running_avg ) );

    if ( $num_samples <= 1 )
    {
      die "You must specify at least 2 samples for avg() calculations.";
      return( undef );
    }



    # REVISIT:
    # If there is trouble with the surrounding calculation then there is
    # little point in carrying on.
    #
    # if ( $CC::ERROR_STATUS )

    #
    if (  ! FT::timer($interval) )
    {
      return( $self->[$DATA_AVG] );
    }

    if ( ! defined($value) || $value eq "undef" )
    {
      #
      # If the current value is indeterminate then we can not 
      # determine the average, so reset everything.
      #
      $DEBUG && 
        TraceFuncs::debug("Data is invalid. Reset calculations." );
      $self->[$DATA_COUNT] = 0;
      $self->[$DATA_VALUE] = undef;
      $self->[$DATA_TOTAL] = undef;
      $self->[$DATA_AVG]   = undef;
      #$die "Input data is undefined for avg calculation.";
    }
    elsif ( ! defined($self->[$DATA_VALUE]) )
    {
      #
      # If first time the monitor is called or if we have the first valid value
      # then we can start totalizing.  
      #
      $DEBUG && 
        TraceFuncs::debug("Start of interval - but no previous value yet" );


      $self->[$DATA_COUNT] = 1;
      $self->[$DATA_VALUE] = $value;
      $self->[$DATA_TOTAL] = $value;
      $self->[$DATA_AVG]   = undef;
    }
    else
    {
      $last_count = $self->[$DATA_COUNT];
      ++$self->[$DATA_COUNT];
  
      $self->[$DATA_VALUE] = $value;
      if ( $last_count < $num_samples )
      {
        #
        # If we are in the middle of the sampling period then just update the
        # $running_total and return the average from the last averaging period.
        #
        $DEBUG && TraceFuncs::debug (
                    "In middle of averaging interval, " .
                    "count is now " . $self->[$DATA_COUNT] );

        $self->[$DATA_TOTAL] += $value;
      }

      else
      {
        #
        # Since we are at the end of the averaging period calculate the average.
        #
        $DEBUG && 
          TraceFuncs::debug(
            "End/Beginning of averaging period so calculate average" );


        $self->[$DATA_COUNT]   = 1;

        $self->[$DATA_AVG] = 
          ( 0.0 + $value + $self->[$DATA_TOTAL] ) / $num_samples;

        $self->[$DATA_TOTAL] = 
          ( $is_running_avg ) ? $self->[$DATA_AVG] : $value ;

        $DEBUG && TraceFuncs::debug( 
            "(0.0 + " .
            $value . " + " .
            $self->[$DATA_TOTAL] . " ) / " .
            $num_samples );
      }
    }

    $DEBUG && TraceFuncs::debug( 
            "avg = " . 
            (( defined($self->[$DATA_AVG]) ) ? $self->[$DATA_AVG] : "undef" ));

    return( $self->[$DATA_AVG] );
  }

  # -------------------------------------------------------------------------
  sub min
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self           = shift;
    my $num_samples    = shift;
    my $interval       = shift;
    my $value          = shift;
    my $resource       = shift;

    my $key;
    my $last_count = 0;

    # my( $l_value_str )  = ( defined( $l_value ) ) ? "$l_value" : "undef";


    $resource       = $FT::RESOURCE if ( ! defined( $resource ) );
    $self->[$DATA_END]   = 0;


    # REVISIT:
    # If there is trouble with the surrounding calculation then there is
    # little point in carrying on.
    #
    # if ( $CC::ERROR_STATUS )

    #
    if (  ! FT::timer($interval) )
    {
      return( $self->[$DATA_MIN] );
    }

    if ( ! defined($value) || $value eq "undef" )
    {
      #
      # If the current value is indeterminate then we can not 
      # determine the minimun, so reset everything.
      #
      $DEBUG && 
        TraceFuncs::debug("Data is invalid. Reset calculations." );
      $self->[$DATA_COUNT] = 0;
      $self->[$DATA_VALUE] = undef;
      $self->[$DATA_MIN] = undef;
      #die "Input data is undefined for min calculation number";
    }
    elsif ( ! defined($self->[$DATA_VALUE]) )
    {
      #
      # If first time the monitor is called or if we have the first valid value
      # then we can start totalizing.  
      #
      $DEBUG && 
        TraceFuncs::debug("Start of interval - but no previous value yet" );
      $self->[$DATA_COUNT] = 1;
      $self->[$DATA_VALUE] = $value;
      $self->[$DATA_MIN]   = undef;
    }
    else
    {
      $last_count = $self->[$DATA_COUNT];
      ++ $self->[$DATA_COUNT];
  
      if ( $last_count < $num_samples )
      {
        #
        # If we are in the middle of the sampling period then just update the
        # $min and return the min from the last averaging period.
        #
        $DEBUG && TraceFuncs::debug (
                    "In middle of averaging interval, " .
                    "count is now " . $self->[$DATA_COUNT] );

        $self->[$DATA_VALUE] = $value if ( $value < $self->[$DATA_VALUE] );
      }
      else
      {
        #
        # Since we are at the end of the averaging period calculate the average.
        #
        $DEBUG && 
          TraceFuncs::debug(
            "End/Beginning of averaging period so calculate average" );

        $self->[$DATA_COUNT] = 1;
        $self->[$DATA_END]   = 1;
        $self->[$DATA_MIN]   = $self->[$DATA_VALUE];
        $self->[$DATA_VALUE] = $value;
      }
    }

    $DEBUG && TraceFuncs::debug( 
            "min = " . 
            (( defined($self->[$DATA_MIN]) ) ? $self->[$DATA_MIN] : "undef" ));

    return( $self->[$DATA_MIN] );
  }

  # -------------------------------------------------------------------------
  sub bl_min
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self           = shift;
    my $num_samples    = shift;
    my $interval       = shift;
    my $name           = shift;
    my $resource       = shift;

    my $bl_file = $FT::MONITOR::NAME . ".bl";

    local(*value) = $name;
    $name =~ s/^\*.*:://;
    my $baseline = "";

    $DEBUG && TraceFuncs::debug("$name = $value");
    my $avg_value = $self->min($num_samples, $interval, $value, $resource);


    #
    # If end of averaging period - record the baseline.
    #
    $DEBUG && TraceFuncs::debug("$end = " . $self->end());
    if ( $self->end() )
    {
      $FT::MONITOR::BASELINED = time();
      $avg_value = FT::round( ( $avg_value, 4) );
      my $resource = $FT::RESOURCE;
      $resource =~ s/\\/\\\\/g;
      $baseline = "\$" . $name . "{'" . $resource . 
                  "'} = " . $avg_value . ";\n";
      if ( ! defined $_BASELINE || $_BASELINE =~ /$resource/ )
      {
        open(BL, "> $bl_file") || die "could not open '$bl_file': $!";
        $_BASELINE = $resource . "\n";
        print BL $baseline;
      }
      else
      {
        open(BL, ">> $bl_file") || die "could not open '$bl_file': $!";
        $_BASELINE = $_BASELINE . $resource . "\n";
        print BL $baseline;
      }
    }
    $DEBUG && TraceFuncs::debug("base_line =" . $avg_value);
    
    return($avg_value); 
  }

  # -------------------------------------------------------------------------
  sub max
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self           = shift;
    my $num_samples    = shift;
    my $interval       = shift;
    my $value          = shift;
    my $resource       = shift;

    my $key;
    my $last_count = 0;

    # my( $l_value_str )  = ( defined( $l_value ) ) ? "$l_value" : "undef";


    $resource       = $FT::RESOURCE if ( ! defined( $resource ) );
    $self->[$DATA_END]   = 0;


    # REVISIT:
    # If there is trouble with the surrounding calculation then there is
    # little point in carrying on.
    #
    # if ( $CC::ERROR_STATUS )

    #
    if (  ! FT::timer($interval) )
    {
      return( $self->[$DATA_MAX] );
    }

    if ( ! defined($value) || $value eq "undef" )
    {
      #
      # If the current value is indetermaxate then we can not 
      # determaxe the maximun, so reset everything.
      #
      $DEBUG && 
        TraceFuncs::debug("Data is invalid. Reset calculations." );
      $self->[$DATA_COUNT] = 0;
      $self->[$DATA_VALUE] = undef;
      $self->[$DATA_MAX] = undef;
      #die "Input data is undefined for max calculation number";
    }
    elsif ( ! defined($self->[$DATA_VALUE]) )
    {
      #
      # If first time the monitor is called or if we have the first valid value
      # then we can start totalizing.  
      #
      $DEBUG && 
        TraceFuncs::debug("Start of interval - but no previous value yet" );
      $self->[$DATA_COUNT] = 1;
      $self->[$DATA_VALUE] = $value;
      $self->[$DATA_MAX]   = undef;
    }
    else
    {
      $last_count = $self->[$DATA_COUNT];
      ++ $self->[$DATA_COUNT];
  
      if ( $last_count < $num_samples )
      {
        #
        # If we are in the middle of the sampling period then just update the
        # $max and return the max from the last averaging period.
        #
        $DEBUG && TraceFuncs::debug (
                    "In middle of averaging interval, " .
                    "count is now " . $self->[$DATA_COUNT] );

        $self->[$DATA_VALUE] = $value if ( $value > $self->[$DATA_VALUE] );
      }
      else
      {
        #
        # Since we are at the end of the averaging period calculate the average.
        #
        $DEBUG && 
          TraceFuncs::debug(
            "End/Beginning of averaging period so calculate average" );

        $self->[$DATA_COUNT]   = 1;
        $self->[$DATA_END]   = 1;
        $self->[$DATA_VALUE] = $value;
        $self->[$DATA_MAX] =  $self->[$DATA_VALUE];
      }
    }

    $DEBUG && TraceFuncs::debug( 
            "max = " . 
            (( defined($self->[$DATA_MAX]) ) ? $self->[$DATA_MAX] : "undef" ));

    return( $self->[$DATA_MAX] );
  }

  # -------------------------------------------------------------------------
  sub bl_max
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self           = shift;
    my $num_samples    = shift;
    my $interval       = shift;
    my $name           = shift;
    my $resource       = shift;

    my $bl_file = $FT::MONITOR::NAME . ".bl";

    local(*value) = $name;
    $name =~ s/^\*.*:://;
    my $baseline = "";

    $DEBUG && TraceFuncs::debug("$name = $value");
    my $avg_value = $self->max($num_samples, $interval, $value, $resource);


    #
    # If end of averaging period - record the baseline.
    #
    $DEBUG && TraceFuncs::debug("$end = " . $self->end());
    if ( $self->end() )
    {
      $FT::MONITOR::BASELINED = time();
      $avg_value = FT::round( ( $avg_value, 4) );
      my $resource = $FT::RESOURCE;
      $resource =~ s/\\/\\\\/g;
      $baseline = "\$" . $name . "{'" . $resource . 
                  "'} = " . $avg_value . ";\n";
      if ( ! defined $_BASELINE || $_BASELINE =~ /$resource/ )
      {
        open(BL, "> $bl_file") || die "could not open '$bl_file': $!";
        $_BASELINE = $resource . "\n";
        print BL $baseline;
      }
      else
      {
        open(BL, ">> $bl_file") || die "could not open '$bl_file': $!";
        $_BASELINE = $_BASELINE . $resource . "\n";
        print BL $baseline;
      }
    }
    $DEBUG && TraceFuncs::debug("base_line =" . $avg_value);
    
    return($avg_value); 
  }

# =====================================================================
package FTMON::DeltaCalculation;

  @FTMON::DeltaCalculation::ISA = ("FTMON::Calculation");

  $DEBUG = 0 if ( ! defined($FTMON::DeltaCalculation::DEBUG) );

  $_LAST_ATTRIB = $FTMON::Calculation::_LAST_ATTRIB + 2;
  my($DATA_DELTA,
     $DATA_1ST_VALUE,) = 
      ($FTMON::Calculation::_LAST_ATTRIB + 1 .. $_LAST_ATTRIB);


  # -------------------------------------------------------------------------
  sub new
  {
    $DEBUG && TraceFuncs::trace(my $f);
    my $proto = shift;

    my $class = ref($proto) || $proto;
    
  my $id = $FTMON::CalculationManager::SINGLETON->next_calc_id();

  if ( ! ( $self = $FTMON::Base::SINGLETON->find_instance($class, $id )) )
  {
    $DEBUG && TraceFuncs::debug("New calculation - $id");
    $self = $class->SUPER::new($id);
    bless($self, $class);
  }
  else
  {
    $DEBUG && TraceFuncs::debug("Used existing calculation - $id");
    return($self);
  }


    $self->[$DATA_COUNT] =  0;
    $self->[$DATA_VALUE] =  0;
    $self->[$DATA_1ST_VALUE] = 0; 
    $self->[$DATA_DELTA]   =  0;

    return($self);
  }

  # ----------------------------------------------------------------------
  sub DESTROY
  {
    my $self  = shift;
    $self->SUPER::DESTROY();
  }

  # ----------------------------------------------------------------------
  sub delta
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self        = shift;
    my $num_samples = shift;
    my $interval    = shift;
    my $value       = shift;
    my $resource    = shift;

    my $key;
    my $last_count = 0;

    # my( $l_value_str )  = ( defined( $l_value ) ) ? "$l_value" : "undef";


    $resource       = $FT::RESOURCE if ( ! defined( $resource ) );

    if ( $num_samples <= 0 )
    {
      die "You must specify at least 1 sample for delta() calculations.";
      return( undef );
    }

    if ( ! FT::timer($interval))
    {
      return( $self->[$DATA_DELTA] );
    }

    # REVISIT:
    # If there is trouble with the surrounding calculation then there is
    # little point in carrying on.
    #
    # if ( $CC::ERROR_STATUS )


    if ( ! defined($value) || $value eq "undef" )
    {
      #
      # If the current value is indeterminate then we can not 
      # determine the delta, so reset everything.
      #
      $DEBUG && 
        TraceFuncs::debug("Data is invalid. Reset calculations." );
      $self->[$DATA_COUNT] = 0;
      $self->[$DATA_VALUE] = undef;
      $self->[$DATA_1ST_VALUE] = undef;
      $self->[$DATA_DELTA] = undef;
    }
    elsif ( ! defined($self->[$DATA_1ST_VALUE]) )
    {
      #
      # If first time the monitor is called or if we have the first valid value
      # then we can start determining the slope.
      #
      $DEBUG && 
        TraceFuncs::debug("Start of interval - but no previous value yet" );
      $self->[$DATA_COUNT] = 0;
      $self->[$DATA_1ST_VALUE] = $value;
      $self->[$DATA_DELTA] = undef;
    }
    else
    {
      $last_count = $self->[$DATA_COUNT];
      ++ $self->[$DATA_COUNT];
  
      if ( $last_count < $num_samples )
      {
        #
        # If we are in the middle of the sampling period then just update the
        # and return the delta from the previous period.
        #
        $DEBUG && TraceFuncs::debug (
                    "In middle of delta interval, " .
                    "count is now " . $self->[$DATA_COUNT] );
      }

      else
      {
        #
        # Since we are at the end of the averaging period calculate the average.
        #
        $DEBUG && 
          TraceFuncs::debug(
            "End/Beginning of period so calculate delta" );


        $self->[$DATA_COUNT]     = 0;
        $self->[$DATA_DELTA]     =  $value - $self->[$DATA_1ST_VALUE];
        $self->[$DATA_1ST_VALUE] = $value; 
        $DEBUG && TraceFuncs::debug( 
            $value . " - " .
            $self->[$DATA_1ST_VALUE] );
      }
    }

    $DEBUG && TraceFuncs::debug( 
            "delta = " . 
            (( defined($self->[$DATA_DELTA]) ) 
                ? $self->[$DATA_DELTA] : "undef" ));

    return( $self->[$DATA_DELTA] );
  }

# =====================================================================
package FTMON::MonotCalculation;

  @FTMON::MonotCalculation::ISA = ("FTMON::Calculation");

  $DEBUG = 0 if ( ! defined($FTMON::MonotCalculation::DEBUG) );

  $_LAST_ATTRIB = $FTMON::Calculation::_LAST_ATTRIB + 2;
  my($DATA_RUN_MONOT,
     $DATA_MONOT,
     ) =
      ( $FTMON::Calculation::_LAST_ATTRIB + 1 .. $_LAST_ATTRIB );

  # -------------------------------------------------------------------------
  sub new
  {
    $DEBUG && TraceFuncs::trace(my $f);
    my $proto = shift;

    my $class = ref($proto) || $proto;
    
  my $id = $FTMON::CalculationManager::SINGLETON->next_calc_id();

  if ( ! ( $self = $FTMON::Base::SINGLETON->find_instance($class, $id )) )
  {
    $DEBUG && TraceFuncs::debug("New calculation - $id");
    $self = $class->SUPER::new($id);
    bless($self, $class);
  }
  else
  {
    $DEBUG && TraceFuncs::debug("Used existing calculation - $id");
    return($self);
  }


    $self->[$DATA_COUNT] =  0;
    $self->[$DATA_VALUE] =  0;
    $self->[$DATA_RUN_MONOT] = 0; 
    $self->[$DATA_MONOT]   =  0;

    return($self);
  }

  # ----------------------------------------------------------------------
  sub DESTROY
  {
    my $self  = shift;
    $self->SUPER::DESTROY();
  }

  # -------------------------------------------------------------------------
  sub monot
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self        = shift;
    my $num_samples = shift;
    my $interval    = shift;
    my $value       = shift;
    my $resource    = shift;
  
    my $key;
    my $last_count = 0;
    my $this_monot;
    my $this_slope;

    my $monot = 0;
  
    $resource = $FT::RESOURCE if ( ! defined($resource) );

 
    if ( $num_samples <= 0 )
    {
       die "You must specify at least 1 samples for monot() calculations.";
       return( undef );
    }


    # REVISIT:
    # If there is trouble with the surrounding calculation then there is
    # little point in carrying on.
    #
    # if ( $CC::ERROR_STATUS )

    if ( ! FT::timer($interval) )
    {
      return( $self->[$DATA_MONOT] );
    }
  
    if ( ! defined($value) || $value eq "undef" )
    {
      #
      # If the current value is indeterminate then we can not 
      # determine the average, so reset everything.
      #
      $DEBUG && 
        TraceFuncs::debug("Data is invalid. Reset calculations." );
      $self->[$DATA_COUNT]      = 0;
      $self->[$DATA_VALUE]      = undef;
      $self->[$DATA_RUN_MONOT]  = undef;
      $self->[$DATA_MONOT] = undef;
      #die "Input data is undefined for monot calculation number";
    }
    elsif ( ! defined($self->[$DATA_VALUE]) )
    {
      #
      # If first time the monitor is called or if we have the first valid value
      # then we can start totalizing.  
      
      $DEBUG && 
        TraceFuncs::debug("Start of interval - but no previous value yet" );
      $self->[$DATA_RUN_MONOT] = 0;
      $self->[$DATA_MONOT]     = 0;
      $self->[$DATA_COUNT]     = 1;
      $self->[$DATA_VALUE]     = $value;
    }
    else
    {
      #
      # Determine monotonosity.
      #
      $this_slope = $value - $self->[$DATA_VALUE];
      $self->[$DATA_VALUE] = $value;

      $DEBUG && 
        TraceFuncs::debug("this_slope($this_slope) = value($value) - last(" .
                           $self->[$DATA_VALUE], ")" );

      if ( $this_slope == 0 )
      {
        $this_monot = 0;
      }
      elsif ( $this_slope > 0 )
      { 
        $this_monot = 1;
      }
      else
      {
        $this_monot = -1;
      }
      $DEBUG && 
        TraceFuncs::debug("this_monot=" . $this_monot );

      $last_count = $self->[$DATA_COUNT];
      if ( defined($self->[$DATA_RUN_MONOT]) )
      {
        $run_monot = 
          ( $last_count == 0 ||
          ( defined $self->[$DATA_RUN_MONOT] &&
            $this_monot == $self->[$DATA_RUN_MONOT] ) ) 
                ? $this_monot : 0;
        $self->[$DATA_RUN_MONOT] = $run_monot;
      }

      $DEBUG && 
        TraceFuncs::debug("run_monot =" . $run_monot  );
 
      # Increment last count to current sample count.

      if ( $last_count++ >= $num_samples )
      {
        #
        # Since we are at the end of the averaging period calculate the monot
        #
        $DEBUG && 
          TraceFuncs::debug(
            "End/Beginning of period so calculate monot" );
        
        $self->[$DATA_COUNT]     = $last_count = 0;
        $self->[$DATA_RUN_MONOT] = $this_monot;
        $self->[$DATA_MONOT]     = $run_monot;
      }
      else
      {
        $DEBUG && TraceFuncs::debug (
                    "In middle of monot interval, " .
                    "count is now " . $self->[$DATA_COUNT] );

        
        $self->[$DATA_RUN_MONOT] = $run_monot;
        $self->[$DATA_MONOT]     = 
          ( defined($self->[$DATA_MONOT]) ) ? $self->[$DATA_MONOT] : 0;
        $self->[$DATA_COUNT]     = $last_count;
      }

    }


    $DEBUG && TraceFuncs::debug( 
            "monot = " . 
            (( defined($self->[$DATA_MONOT]) ) 
                ? $self->[$DATA_MONOT] : "undef" ));

    return( $self->[$DATA_MONOT] );
  }

# ================================================================
package FT::BL;

  # -------------------------------------------------------------------------
  sub min
  {
    my $avg = FTMON::AvgCalculation->new();
    $FTMON::CalculationManager::SINGLETON->register_calculation($avg);
    $avg->bl_min(@_);
  }

  sub max
  {
    my $avg = FTMON::AvgCalculation->new();
    $FTMON::CalculationManager::SINGLETON->register_calculation($avg);
    $avg->bl_max(@_);
  }

# ================================================================
package FT;

  $DEBUG = 0 if ( ! defined($FT::DEBUG) );

  $NON_TRADING_DATES = {};

  $TRADING_DAYS = [ "Mon", "Tue", "Wed", "Thr", "Fri" ];
  $FT::TRADING_START = "08:30";
  $FT::TRADING_STOP  = "18:00";


  # -------------------------------------------------------------------------
  sub avg
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $avg = FTMON::AvgCalculation->new();
    $FTMON::CalculationManager::SINGLETON->register_calculation($avg);
    $avg->avg(@_);
  }

  # -------------------------------------------------------------------------
  sub min
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $min = FTMON::AvgCalculation->new();
    $FTMON::CalculationManager::SINGLETON->register_calculation($avg);
    $mon->min(@_);
  }

  # -------------------------------------------------------------------------
  sub max
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $max = FTMON::AvgCalculation->new();
    $FTMON::CalculationManager::SINGLETON->register_calculation($avg);
    $max->max(@_);
  }


  # -------------------------------------------------------------------------
  sub delta
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $delta = FTMON::DeltaCalculation->new();
    $FTMON::CalculationManager::SINGLETON->register_calculation($delta);
    $delta->delta(@_);
  }

  # -------------------------------------------------------------------------
  sub monot
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $monot = FTMON::MonotCalculation->new();
    $FTMON::CalculationManager::SINGLETON->register_calculation($monot);
    $monot->monot(@_);
  }


  # -------------------------------------------------------------------------
  # round 
  #     - Rounds the specified float to specified number of decimal places.
  # -------------------------------------------------------------------------
  sub round
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $value = shift;
    my $dec_places = shift;

    $dec_places = 0 if ( ! defined( $dec_places) );

    my $fmt_str = '%.' . $dec_places . "f";

    return $value if ( $value !~ /[\d]+\.[\d+]/ );
    $value =~ s/([\d]+\.[\d]+)/"sprintf('$fmt_str', $1)"/eeg;

    return( $value );
  }

  # -------------------------------------------------------------------------
  sub addr2host
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $l_address = shift;
    my $l_host_name;

    $l_address_pack = pack( "c4", split( /\./, $l_address));

    ( $l_host_name ) = gethostbyaddr( $l_address_pack, AF_INET );

    return( $l_host_name );
  }

  # -------------------------------------------------------------------------
  sub m
  {
    my $value = shift;
    my $regex = shift;
    return ( $value =~ /$regex/ );
  }

  # -------------------------------------------------------------------------
  sub match
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $l_file_name = shift;
    my $l_regex = shift;

    my @l_variables = ();

    $FT::MATCH_COUNT = 0;
    $FT::MATCH_LINE = 0;
    $FT::MATCH_1 = "";
    $FT::MATCH_2 = ""; 
    $FT::MATCH_3 = "";
    $FT::MATCH_4 = "";

    $DEBUG && TraceFuncs::debug(
           "match( \'$l_file_name\', \'$l_regex\' )" );
  
    open( REGEX, "< $l_file_name" ) || die "Could not open '$l_file_name': $!";

    while ( <REGEX> )
    {
      if ( /$l_regex/ )
      {
        $DEBUG && TraceFuncs::debug("match");
        $FT::MATCH_COUNT++;
        $FT::MATCH_LINE =  $.;
        $FT::MATCH_1 = $1 if ( defined( $1 ) );
        $FT::MATCH_2 = $2 if ( defined( $2 ) );
        $FT::MATCH_3 = $3 if ( defined( $3 ) );
        $FT::MATCH_4 = $4 if ( defined( $4 ) );
      }
    }

    close( REGEX );

    $DEBUG && TraceFuncs::debug("return $FT::MATCH_COUNT");
    return( $FT::MATCH_COUNT );
  }

  ## ---------------
  sub is_trading
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $non_trading_dates = shift;
    my $trading_days = shift;
    my $trading_start = shift;
    my $trading_end = shift;

    $non_trading_dates = $FT::NON_TRADING_DATES 
       if ( ! defined $non_trading_dates );
    $trading_days = $FT::TRADING_DAYS if ( ! defined $trading_days );
    $trading_start = $FT::TRADING_START if ( ! defined $trading_start );
    $trading_end = $FT::TRADING_STOP if ( ! defined $trading_end );

    @non_trading_dates = keys %{$non_trading_dates};

    my @non_trading_date = 
       grep { "${FT::dd}/${FT::mo}/${FT::yyyy}" eq $_ } @non_trading_dates;
    if ( @non_trading_date )
    {
      $FT::TRADING_MSG{$FT::VENDOR . "::" . $FT::PRODUCT} = 
         "NON_TRADING: " . $non_trading_dates->{$non_trading_date[0]};
      return 0;
    }

    my @trading_day = grep { "${FT::Day}" eq $_ } @$trading_days;
    if ( ! @trading_day )
    {
      $FT::TRADING_MSG{$FT::VENDOR . "::" . $FT::PRODUCT} = 
         "NON_TRADING: " . $FT::Day;
      return 0;
    }

    die "trading_start time ($trading_start) is invalid" 
       if ( $trading_start !~ /^(\d+):(\d+)$/ );

    my $start_hour = $1;
    my $start_minute = $2;
    $start_hour =~ s/^0//g;
    $start_minute =~ s/^0//g;

    my $start_trading_mins = $start_hour * 60 + $start_minute;


    die "trading_end time ($trading_end) is invalid" 
       if ( $trading_end !~ /^(\d+):(\d+)$/ );
    my $end_hour = $1;
    my $end_minute = $2;
    $end_hour =~ s/^0//g;
    $end_minute =~ s/^0//g;
    my $end_trading_mins = $end_hour * 60 + $end_minute;


    my $current_mins = $FT::N_HOUR * 60 + $FT::N_MIN;

    if ( $current_mins < $start_trading_mins )
    {
      $FT::TRADING_MSG{$FT::VENDOR . "::" . $FT::PRODUCT} = 
         "NON_TRADING: Trading commences in at $FT::TRADING_START (In " . 
         FT::days2str( ($start_trading_mins - $current_mins) / 24 / 60 ) .
         ").";
      return 0;
    }

    if ( $current_mins > $end_trading_mins )
    {
      $FT::TRADING_MSG{$FT::VENDOR . "::" . $FT::PRODUCT} = 
         "NON_TRADING: Trading ended at $FT::TRADING_STOP (" . 
         FT::days2str( ($current_mins - $end_trading_mins) / 24 / 60 ) . 
         " ago).";
      return 0;
    }

    $FT::TRADING_MSG{$FT::VENDOR . "::" . $FT::PRODUCT} = 
       "TRADING " .
       " ${FT::dd}/${FT::mo}/${FT::yyyy}";
    return 1;

  }

  ## ---------------
  sub str2secs
  {
    $DEBUG && TraceFuncs::trace(my $f);
    my $l_time_str = shift;
  
    my $l_dy = 0;
    my $l_hh = 0;
    my $l_mm = 0;
    my $l_ss = 0;
    my $l_time_in_secs = undef;
    my $l_valid_str = 0;
  
  
    $l_time_str = "" if (! defined( $l_time_str ) );
    $l_time_str =~ s/\-/ /g;
  
    if ( $l_time_str =~ /^.*\b(\d+) day/ )
    {
      $l_dy = $1;
      $l_valid_str = 1;
    }
  
    if ( $l_time_str =~ /^.*\b(\d+) hr/ )
    {
      $l_hh = $1;
      $l_valid_str = 1;
    }
  
    if ( $l_time_str =~ /^.*\b(\d+)\s+(\d+) hr/ )
    {
      $l_dy = $1;
      $l_hh = $2;
      $l_valid_str = 1;
    }
  
  
    if ( $l_time_str =~ /^.*\b(\d+)\s+(\d+) min/ )
    {
      $l_dy = $1;
      $l_mm = $2;
      $l_valid_str = 1;
    }
  
    if ( $l_time_str =~ /^.*\b(\d+) min/ )
    {
      $l_mm = $1;
      $l_valid_str = 1;
    }
  
    if ( $l_time_str =~ /^.*\b(\d+)\s+(\d+):(\d+):(\d+)\b/ )
    {
      $l_dy = $1;
      $l_hh = $2;
      $l_mm = $3;
      $l_ss = $4;
      $l_valid_str = 1;
    }
    elsif ( $l_time_str =~ /^.*\b(\d+):(\d+):(\d+)\b/ )
    {
      $l_hh = $1;
      $l_mm = $2;
      $l_ss = $3;
      $l_valid_str = 1;
    }
    elsif ( $l_time_str =~ /^.*\b(\d+)\s+(\d+):(\d+)\b/ )
    {
      $l_dy = $1;
      $l_hh = $2;
      $l_mm = $3;
      $l_valid_str = 1;
    }
    elsif ( $l_time_str =~ /^.*\b(\d+):(\d+)\b/ )
    {
      $l_hh = $1;
      $l_mm = $2;
      $l_valid_str = 1;
    }
  
    if ( ! $l_valid_str )
    {
      die "$l_valid_str: Invalid usage for strToSecs() function";
    }
  
    $l_time_in_secs =
          $l_ss + 60 * ( $l_mm +
                         60 * ( $l_hh +
                                ( 24 * $l_dy ) ) );
  
    $DEBUG && TraceFuncs::debug( 
       "$l_time_in_secs = \n" .
          "$l_ss + 60 * ( $l_mm +\n" .
          "               60 * ( $l_hh +\n" .
          "                      ( 24 * $l_dy ) ) )" );
  
    $DEBUG && TraceFuncs::debug( $l_time_in_secs );
    return ( $l_time_in_secs );
  }


  ## ---------------
  sub days2str
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $decimadays = shift;
    my $return_str = "";
    my $totaminutes;
    my $plural = "";;
    my $days = 0;
    my $hours = 0;
    my $mins = 0;
  
    $totaminutes =  $decimadays * 60 * 24;
    $days          = int( $totaminutes / ( 24 * 60 ) );
    $plural        = "s" if ( $days > 1 );
    $return_str    = $days . " day" . $plural if ( $days > 0 );
  
    $totaminutes = $totaminutes - $days * 24 * 60;
    $hours         = int( $totaminutes / 60 );
    $plural        = "s" if ( $hours > 1 );
    $return_str    = $return_str . " " . $hours . " hour" . $plural 
                              if ( $hours > 0 );
  
    $mins       = int( $totaminutes - $hours *  60 );
    $plural     = "s" if ( $mins > 1 );
    $return_str =  $return_str . " " . $mins . " minute" . $plural 
                              if ( $mins > 0 );
  
    $return_str = "less than minute" if ( ! $return_str );
  
    return $return_str;
  }


  # -----------------------------------------------------------------------
  #  cmd - wrapper for extracting monitor values from any command 
  #        also does error handling.
  # -----------------------------------------------------------------------
  sub cmd
  {
    my( $l_cmd, $l_values ) = @_;
    $DEBUG && TraceFuncs::trace(my $f);

    $l_values = \@FT::VALUES if ( ! defined($l_values) );
    @{$l_values} = ();


    my $l_prefilter = $FT::CMD::PREFILTER
       if (defined($FT::CMD::PREFILTER));
  
    my $l_header_filter = $FT::CMD::HEADER_FILTER
       if (defined($FT::CMD::HEADER_FILTER));
  
    my $l_error_filter = $FT::CMD::ERROR_FILTER
       if (defined($FT::CMD::ERROR_FILTER));
  
    my $l_col_sep = ",";
    $l_col_sep = $FT::CMD::COL_SEP
       if (defined($FT::CMD::COL_SEP));
    
    my $l_max_error_lines  = 0;
    $l_max_error_lines = $FT::CMD::MAX_ERROR_LINES
       if (defined($FT::CMD::MAX_ERROR_LINES));
    
    my $l_header_end_line  = 0;
    $l_header_end_line = $FT::CMD::HEADER_END 
       if (defined($FT::CMD::HEADER_END));
   

    my $l_row;

    my $l_col;
    my @l_cols;
    my $i;
 
    $FT::LINE = $ENV{'FT_LINE'} = 0;
    $FT::ROW  = $ENV{'FT_ROW'}  = "";
  
    my $l_out_file   = 
       $FTMON::Environment::SINGLETON->log_dir() . "/" .  "ftmon.out";
    unlink($l_out_file);

    if ( ref($l_cmd) eq "CODE" )
    {
      open(OLDOUT, ">&STDOUT");
      open(STDOUT, "> $l_out_file");

      &$l_cmd();

      close( STDOUT );
      open( STDOUT, ">&OLDOUT" );
      close( OLDOUT );

      $l_cmd = $l_out_file;
    }

    if ( ! open( CMD_FH, $l_cmd ) )
    {
      $FT::ERROR_MSG    = "ERROR: FT::CMD - open($l_cmd) - $!";
      $FT::ERROR_STATUS = $?;
      return(0);
    }


    $l_error_str = "";
    while( <CMD_FH> ) 
    {
      chop;
  
      next if ( /^\s+$/ );
      next if ( /^$/ );
  
      $l_row  = $_;
      $FT::ROW  = $ENV{'CT_ROW'}  = $l_row;
      $FT::LINE = $ENV{'CT_LINE'} = $.;
  
      $l_error_str .= " $_" if ( $. < $l_max_error_lines );

  
      #
      # check if this line matches an inline header.
      #
      next if ( defined($l_header_filter) && &${l_header_filter}() );
  
      #
      # check if this line is an error.
      #
      if ( defined($l_error_filter) && &${l_error_filter}() )
      {
        $FT::ERROR_MSG    = "ERROR: FT::CMD - " . $l_row;
        $FT::ERROR_STATUS = 0;
        return(0);
      }

      #
      # skip over header.
      #
      next if ( $. <= $l_header_end_line );
  
      $_ = $l_row;
      &${l_prefilter}() if (defined($l_prefilter));
      $l_row = $_;

      #
      # Split row and cleanup spaces surrounding column entries
      #
      @l_cols = split( /$l_col_sep/, "$l_row " );
      $l_cols[$#l_cols] =~ s/ $//;
  
      foreach( @l_cols )
      {
        $_ =~ s/^\s*//g;
        $_ =~ s/\s*$//g;
      }
      next if ( ! @l_cols );
      
      push( @{$l_values}, [ @l_cols ] );
   
    }
   
    #
    # Check for any errors returned by the command
    #

    if ( ! close(CMD_FH) )
    {
      $FT::ERROR_STATUS  = $?;
      $FT::ERROR_MSG     = "ERROR: FT::CMD - close() - " . $!;
      return(0);
    }
    
    return(1);
  }

  # ---------------------
  sub encrypt
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my( $l_crypt_password, $l_crypt_string ) = @_;
  
    my($l_cs) = Crypt::CipherSaber->new("B0bTheBu1lder");
    my($l_encrypted) = $l_cs->crypt($l_crypt_password, $l_crypt_string);
  
    return encode_base64($l_encrypted);
  }

  # -------------------------------------------------------------------------
  # decrypt
  # -------------------------------------------------------------------------
  sub decrypt
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $l_str = shift;
    my $l_password = shift;
  
    $l_password = $FT::HOSTNAME if ( ! defined($l_password) );
  
    if ( ! defined($l_str) )
    {
      die "decrypt() - You must define a string to decrypt.";
    }
  
    my $l_encrypted = FT::decode_base64($l_str);
  
    my $cs = Crypt::CipherSaber->new("B0bTheBu1lder");
    my $l_decrypt = $cs->crypt($l_password, $l_encrypted);
  
    # if ( ! $l_decrypt || $l_decrypt =~ /\b/ )
    if ( ! $l_decrypt  )
    {
      die "Invalid encryption text specified - $l_str";
    }
  
    return($l_decrypt);
  }


  # -------------------------------------------------------------------------
  # decrypt_file
  # -------------------------------------------------------------------------
  sub decrypt_file
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $l_str = shift;
    my $l_password_file = shift;
  
    my $l_password = $FT::HOSTNAME;
  
    if ( ! defined($l_str) )
    {
      die "decrypt() - You must define a string to decrypt.";
    }
  
    if ( $l_password_file )
    {
      if ( ! open( CRYPT, "< $l_password_file" )  )
      {
        die "Password file '$l_password_file' is not accessable - $!";
      }
  
      while ( <CRYPT> )
      {
        chomp;
        next if ( /^\s*$/ ||  /^\s*#/ );
  
        $l_password = $_;
      }
  
      if ( ! $l_password )
      {
        die "Password not defined in password file '$l_password_file'.";
      }
      close(CRYPT);
    }
  
    my $l_encrypted = FT::decode_base64($l_str);
  
    my $cs = Crypt::CipherSaber->new("B0bTheBu1lder");
    my $l_decrypt = $cs->crypt($l_password, $l_encrypted);
  
    # if ( ! $l_decrypt || $l_decrypt =~ /\b/ )
    if ( ! $l_decrypt  )
    {
      die "Invalid encryption text specified - $l_str";
    }
  
    return($l_decrypt);
  }
  
  
  # ---------------------------------------------------------------------------
  # Code taken from package MIME::Base64.
  #Copyright 1995-1999, 2001 Gisle Aas.
  #
  #This library is free software; you can redistribute it and/or
  #modify it under the same terms as Perl itself.
  #
  #Distantly based on LWP::Base64 written by Martijn Koster
  #<m.koster@nexor.co.uk> and Joerg Reichelt <j.reichelt@nexor.co.uk> and
  #code posted to comp.lang.perl <3pd2lp$6gf@wsinti07.win.tue.nl> by Hans
  #Mulder <hansm@wsinti07.win.tue.nl>
  # ---------------------------------------------------------------------------
  sub decode_base64 ($)
  {
      local($^W) = 0; # unpack("u",...) gives bogus warning in 5.00[123]
  
      my $str = shift;
      $str =~ tr|A-Za-z0-9+=/||cd;            # remove non-base64 chars
      if (length($str) % 4) {
          die "Length of base64 data not a multiple of 4";
      }
      $str =~ s/=+$//;                        # remove padding
      $str =~ tr|A-Za-z0-9+/| -_|;            # convert to uuencoded format
  
      return join'', map( unpack("u", chr(32 + length($_)*3/4) . $_),
                          $str =~ /(.{1,60})/gs);
  }
  
  sub encode_base64 ($;$)
  {
      my $res = "";
      my $eol = $_[1];
      $eol = "\n" unless defined $eol;
      pos($_[0]) = 0;                          # ensure start at the beginning
  
      $res = join '', map( pack('u',$_)=~ /^.(\S*)/, ($_[0]=~/(.{1,45})/gs));
  
      $res =~ tr|` -_|AA-Za-z0-9+/|;               # `# help emacs
      # fix padding at the end
      my $padding = (3 - length($_[0]) % 3) % 3;
      $res =~ s/.{$padding}$/'=' x $padding/e if $padding;
      # break encoded string into lines of no more than 76 characters each
      if (length $eol) {
          $res =~ s/(.{1,76})/$1$eol/g;
      }
      return $res;
  }

  # -------------------------------------------------------------------------
  sub ordered_sev
  {
    $DEBUG && TraceFuncs::trace(my $f);
    my $sev = shift;

    my $i = 0;
    if ( ! defined %FT::SEV::ORDERED || ! defined $FT::SEV::ORDERED{"NOEVENT"} )
    {
      %FT::SEV::ORDERED = ();
      foreach ( @FT::SEV )
      {
        $FT::SEV::ORDERED{$_} = $i;
        $i++;
      }
    }

    if ( defined $FT::SEV::ORDERED{$sev} )
    {
      return $FT::SEV::ORDERED{$sev};
    }
    else
    {
      # REVISIT - should really die.
      return 0;
    }

  }


  # -------------------------------------------------------------------------
  sub system_retry
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my ($l_cmd, $l_retries) = @_;
    my $l_output;
    my $l_error;
    my $l_cmd_str;
    my $i;
    my $l_slot;
    my $l_status;
    my $l_array_sep = $";
    my $l_pid;
  
  
    $l_retries = 0 if ( ! defined( $l_retries ) );
  
  
    $l_cmd_str = "";
    foreach ( @$l_cmd )
    {
      $l_cmd_str .= "$_\n";
      # $_ = substr( $_, 0, $U::MAX_CMD_LEN ) if ( length($_) > $U::MAX_CMD_LEN );
      $_ =~ s/^\s//o;
      $_ =~ s/\s$//o;
      if ( $^O eq "MSWin32" && $_ =~ /\s/ )
      {
        $_ =~ s/\"/\'/og;
        $_ =~ s/\n/,\t/og;
        $_ =~ s/^/\"/o;
        $_ =~ s/$/\"/o;
      }
    }
  
  
  
    for ( $i = 0; $i <= $l_retries; $i++ )
    {
      $l_output = "";
      $l_error = "";
  
      open(SAVEOUT, '>&STDOUT');
      open(SAVEERR, '>&STDERR');
    
      pipe(READ, WRITE);
    
      open(STDOUT, '>&WRITE');
      open(STDERR, '>&STDOUT');
    
      close(WRITE);
    
      $l_pid = system(@{$l_cmd} );
      $l_status = 0xffff & $?;
    
      open(STDOUT, '>&SAVEOUT');
      open(STDERR, '>&SAVEERR');
      close(SAVEOUT);
      close(SAVEERR);
    
      my @l_result= <READ>;
      close(READ);
  
      #waitpid($l_pid, 0 );
      #$l_status = 0xffff & $?;
    
      $" = ' ';
      $l_output = "@l_result";
      $" = ',';
      if ( $l_status )
      {
        #
        # return status error.
        #
        if ( $l_status == 0xff00 )
        {
          $l_error = "Failed: Command does not exist or not accessable.";
        }
        elsif ( $l_status & 0xff00 )
        {
          $l_status >>=8;
          $l_error = "Non zero exit status: $l_status - $l_output";
        }
        else
        {
          if ( $l_status & 0x80 )
          {
            $l_status &= ~0x80;
            $l_error = "coredump from ";
          }
          $l_error .= "signal $l_status.";
        }
        $l_output = "";
      }
  
      last if ( ! $l_error );
    }
  
    if ( $l_error)
    {
      $l_error =~ s/\n//g;
      die $l_error 
    }
  
    return( $l_output );
  }

  sub parse_csv
  {
    my $text = shift;

    my @new = ();
    while ( $text =~ m {
      [\'\"]([^\"\'\\]*(?:\\.[^\"\'\\]*)*)[\'\"],?
         |   ([^,]+),?
         |   ,  }gx )
    {
      push(@new, $+) ;
    }

    if ( substr($text, -1, 1) eq ',' )
    {
      push(@new, undef)
    }

    return(@new);
  }

1;

__END__;

######################## User Documentation ##########################

## To format the following documentation into a more readable format,
## use one of these programs: perldoc; pod2man; pod2html; pod2text.
## For example, to nicely format this documentation for printing, you
## may use pod2man and groff to convert to postscript:

=head1 NAME

FTMON::Calculation - 

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
