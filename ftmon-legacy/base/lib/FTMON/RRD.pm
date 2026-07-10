package FTMON::RRD;
#############################################################################
#                    FTMON (Fast Track Systems Monitor)
#
#   Script: @(#) $RCSfile: RRD.pm,v $ 
# 
#   DESCRIPTION: 
#
#   @(#) Wrapper for the "rrdtool" perl api for use by FTMON
#   @(#) rrdtool was written by Tobias Oetiker <oetiker@ee.ethz.ch> 
#
#   $Source: /cvsroot/ftmon/base2/lib/FTMON/RRD.pm,v $
#
#   $Date: 2003/04/05 03:52:14 $
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
#      PO Box 283
#      Eastwood NSW 2122
#      AUSTRALIA
#      dsheehan@ftmon.org
#      http://ftmon.org
#
#############################################################################
require 5.002;

use RRDs;
use TraceFuncs;
use FTMON::Base;

use FTMON::Calculation;
use FTMON::Scheduler;
use FTMON::Environment;


$DEBUG = 0 if ( ! defined($FTMON::SNMP::DEBUG) );
@FTMON::RRD::ISA = ("FTMON::Base");

# Minutes in a day.
$DAY_MINUTES  = ( 24 * 60 );


$_LAST_ATTRIB = $FTMON::Base::_LAST_ATTRIB + 28;
my(
  # Minimum number of samples to keep in the RRD",
  $MIN_SAMPLES,

  # Directory to keep rrd database in.
  $RRD_DIR,

  # Name of rrd database file
  $RRD_FILE,

  # Data sources
  $DS,

  # Round Robin archive.
  $RRA,

  # defines the sampling rate as a multiple of the monitoring interval.
  $STEP,

  # Width of resulting gif graph in pixels",
  $GRAPH_WIDTH,

  # Height of rrd graph in pixels.
  $GRAPH_HEIGHT,

  # How may STEPs of data to display on graph and keep in RRD
  $WINDOW,

  # How many days of rollover graphs to keep;
  $ROLL_OVER,

  # Scale factor for minute (for testing). Default is 60 seconds"
  # For testing purposes you can adjust how long a minute takes.
  $MINUTE_SF,

  $CONFIG_FILE,


  # --------------------------------------------------
  
  # Defines of the round robin database has been created or not
  $_RRD_CREATED,

  # Full path to the rrd database file.
  $_RRD_PATH,

  # Values of variables Derived from $DS glob.
  $_VALUES,

  # Names of variables Derived from $DS glob.
  $_NAMES,

  # The current step count since the ftmon was started.
  $_CURRENT_STEP,

  # What the current roll over file is.
  $_CURRENT_ROLL_OVER,

  # When not -1 indicates that roll over is to be performed.
  # The value gives the identifier of the rollover file i.e.
  # _CURRENT_ROLL_OVER
  $_ROLL_OVER_INDEX,

  # Time that the last rrd update was performed.
  $_LAST_RUN,

  # Seconds since the last rrd update was performed.
  $_INTERVAL,

  # How may seconds of data to display on graph and keep in RRD
  $_GRAPH_WINDOW,

  $_RRD_MONTHLY_STEP,
  $_RRD_WEEKLY_STEP,

  # The actual monitor run time interval in seconds.
  $_MONITOR_INTERVAL,

  # When the monitor last run.
  $_MONITOR_LAST_RUN,

  # Paths to graphs generated for this RRD
  $_GRAPHS,

  ) = ($FTMON::Base::_LAST_ATTRIB + 1 .. $_LAST_ATTRIB );


# ----------------------------------------------------------------------------
# parse_ds
#  - extracts the @names and (if defined) @ids from the specified
#  @fields.  The @names are then checked to see if they are valid variables
# and the associated current values are returned in @values.
# A unique identifier for the graph is returned as the return value.
# ----------------------------------------------------------------------------
sub parse_ds
{
  my $ds = shift;
  my $names = shift;
  my $values = shift;

  my $ds_key;

  $DEBUG && TraceFuncs::trace(my $f);

  my @ds = @$ds;
  my $name;
  my $i = 0;
  foreach $data_source ( @{$ds} )
  {
    if ( ref($data_source) eq  "ARRAY" )
    {
      foreach $sub_data_source ( @{$data_source} )
      {
        $name = $sub_data_source;
        $name =~ s/^\*.*:://;
        push(@{$names}, $name);

        local(*value) = $sub_data_source;
        push(@{$values}, $value);
        $ds_key = $ds_key . "_" . $name
      }
      $i += 2;
    }
    else
    {
      $name = $data_source;
      $name =~ s/^\*.*:://;
      push(@{$names}, $name);

      local(*value) = $data_source;
      push(@{$values}, $value);
      $ds_key = $ds_key . "_" . $name;
      $i++;
    }
  }

  $ds_key  =  $FT::PACKAGE . "_" . $ds_key . "_" . $FT::RESOURCE;

  $DEBUG && TraceFuncs::debug("key = $ds_key");

  return $ds_key;
}

# ----------------------------------------------------------------------------
sub collect_garbage
{
  $DEBUG && TraceFuncs::trace(my $f);

  my $class = __PACKAGE__;


  my @rrds = ();
  my $rrd;
  my $deleted = 0;;
  $FTMON::Base::SINGLETON->list($class, \@rrds );
  foreach $rrd ( @rrds )
  {

    next if ( ! defined($rrd) );
    my $monitor_interval = $rrd->monitor_interval();
    my $monitor_last_run = $rrd->monitor_last_run();
    my $step = $rrd->step();

    print " monitor_interval = $monitor_interval, step = $step \n";
    if ( $rrd->last_run() < 
	 ( $monitor_last_run - 2 * $monitor_interval * $step )
       )
    {
      # Force perl garbage collection to kick in.
      print "Deleting($last_touched) $class " . $rrd->id() . "\n";
      $rrd->deleted(1);
      $deleted ++;
    }
  }

  return $deleted;
}

# -------------------------------------------------------------
sub new
{
  $DEBUG && TraceFuncs::trace(my $f);

  my $proto  = shift;

  my $ds = shift;
  my $param = shift;

  die "data source must be defined" 
     if (  ! defined $ds ) ;

  die "data source must be ARRAY reference" 
     if (  ref($ds) ne "ARRAY" ) ;

  die "data source parameters must be HASH reference" 
     if ( defined $param && ref($param) ne "HASH" );

  my @names;
  my @values;
  my $rrd_id = parse_ds($ds, \@names, \@values);

  my $class = ref($proto) || $proto;
  my $self = [];

  # Use existing instance if one exists.
  if ( ( $self = $FTMON::Base::SINGLETON->find_instance($class, $rrd_id )) )
  {
    return($self);
  }
  else
  {
    $self = $class->SUPER::new($rrd_id);
  }
  
  $self->[$DS]      = $ds;
  $self->[$_NAMES]  = \@names;
  $self->[$_VALUES] = \@values;

  $self->[$_GRAPHS] = [];

  $self->[$TIME_STAMP] = time();

  my $rrd_dir;
  if ( defined($param->{RRD_DIR} ) )
  {
    $rrd_dir = $param->{RRD_DIR};
    #mkdir($rrd_dir, 0750) if ( ! -d $rrd_dir ) ;
    mkdir($rrd_dir) if ( ! -d $rrd_dir ) ;
  }
  else
  {
    $rrd_dir = $self->[$RRD_DIR] = $FTMON::Environment::SINGLETON->log_dir() ;
    # $rrd_dir = $FT::TEMP;
  }

  my $rrd_file;
  if ( defined($param->{RRD_FILE}) )
  {
    $rrd_file = $self->[$RRD_FILE]  = $param->{RRD_FILE};
  }
  else
  {
    if ( $FT::PACKAGE && $FT::RESOURCE )
    {
      $rrd_file = $FT::PACKAGE . "_" . $FT::RESOURCE;
      $rrd_file =~ s/::/_/g;
      $rrd_file =~ s/[^\w\-\_]//g;
    }
    else
    {
      $rrd_file = $FT::PACKAGE;
      $rrd_file =~ s/::/_/g;
      $rrd_file =~ s/\//__/g;
    }

    # Define the RRD data sources.
    foreach ( @names )
    {
      $rrd_file = $rrd_file . "_" . $_;
    }
    $rrd_file = $rrd_file . ".rrd";
    $self->[$RRD_FILE] = $rrd_file;
  }

  $self->[$_RRD_PATH] = $rrd_dir . "/" . $rrd_file;

  $self->[$MIN_SAMPLES] = 6000;
  $self->[$MIN_SAMPLES] = $param->{MIN_SAMPLES}
      if ( ! defined($param->{MIN_SAMPLES}) );

  $self->[$RRA]  = "";
  $self->[$RRA]  = $param->{RRA} if ( defined($param->{RRA}) ); 

  $self->[$STEP] = 1;
  $self->[$STEP] = $param->{STEP} if ( defined($param->{STEP}) );

  $self->[$GRAPH_WIDTH]  = 700;
  $self->[$GRAPH_WIDTH] = $param->{GRAPH_WIDTH}
      if ( defined($param->{GRAPH_WIDTH}) );

  $self->[$GRAPH_HEIGHT] = 135;
  $self->[$GRAPH_HEIGHT] = $param->{GRAPH_HEIGHT} 
      if ( defined($param->{GRAPH_HEIGHT}) );

  $self->[$WINDOW] = 1400;
  $self->[$WINDOW] = $param->{WINDOW} 
      if ( defined($param->{WINDOW}) );

  $self->[$ROLL_OVER] = 7;
  $self->[$ROLL_OVER] = $param->{ROLL_OVER}  
      if ( defined($param->{ROLL_OVER}) );

  $self->[$MINUTE_SF] = 60;
  $self->[$MINUTE_SF] = $param->{MINUTE_SF}
     if ( defined($param->{MINUTE_SF}) && $param->{MINUTE_SF} );


  $self->[$_RRD_CREATED] = 0;

  $self->[$_CURRENT_STEP] = 0;
  $self->[$_CURRENT_ROLL_OVER] = 0;
  $self->[$_ROLL_OVER_INDEX] = -1;

  $self->[$_INTERVAL] = 0;

  $self->[$_GRAPH_WINDOW] = 0;

  $self->[$_RRD_MONTHLY_STEP] = 0;
  $self->[$_RRD_WEEKLY_STEP] = 0;

  $self->[$_MONITOR_LAST_RUN] = 0;
  $self->[$_MONITOR_LAST_RUN] = $FT::MONITOR::LAST_RUN 
     if ( defined $FT::MONITOR::LAST_RUN );

  $self->[$_MONITOR_INTERVAL] = 0;
  $self->[$_MONITOR_INTERVAL] = $FT::MONITOR::INTERVAL 
     if ( defined $FT::MONITOR::INTERVAL);

  bless($self, $class);

  push( @{$FT::RRD{$FT::PACKAGE}}, $self )
     if ( ! grep { $self->id() eq $_->id() } @{$FT::RRD{$FT::PACKAGE}} );

  return($self);
}

# -------------------------------------------------------------------------
sub monitor_interval
{
  my $self = shift;

  return($self->[$_MONITOR_INTERVAL]);
}

# -------------------------------------------------------------------------
sub monitor_last_run
{
  my $self = shift;

  return($self->[$_MONITOR_LAST_RUN]);
}



# -------------------------------------------------------------------------
sub config_file
{
  my $self = shift;

  if (@_) 
  {
    $self->[$CONFIG_FILE] = shift;
  }

  return($self->[$CONFIG_FILE]);
}

# -------------------------------------------------------------------------
sub min_samples
{
  my $self = shift;

  if (@_) 
  {
    $self->[$MIN_SAMPLES] = shift;
  }

  return($self->[$MIN_SAMPLES]);
}


# -------------------------------------------
sub rrd_dir
{
  my $self = shift;
  return($self->[$RRD_DIR]);
}


# -------------------------------------------
sub rrd_file
{
  my $self = shift;
  return($self->[$RRD_FILE]);
}


# -------------------------------------------
sub step
{
  my $self = shift;

  if (@_) 
  {
    $self->[$STEP] = shift;
  }

  return($self->[$STEP]);
}


# -------------------------------------------
sub graph_width
{
  my $self = shift;

  if (@_) 
  {
    $self->[$GRAPH_WIDTH] = shift;
  }

  return($self->[$GRAPH_WIDTH]);
}


# -------------------------------------------
sub graph_height
{
  my $self = shift;

  if (@_) 
  {
    $self->[$GRAPH_HEIGHT] = shift;
  }

  return($self->[$GRAPH_HEIGHT]);
}


# -------------------------------------------
sub window
{
  my $self = shift;

  if (@_) 
  {
    $self->[$WINDOW] = shift;
  }

  return($self->[$WINDOW]);
}

# -------------------------------------------
sub graph_window
{
  my $self = shift;

  if (@_) 
  {
    $self->[$_GRAPH_WINDOW] = shift;
  }

  return($self->[$_GRAPH_WINDOW]);
}



# -------------------------------------------
sub roll_over
{
  my $self = shift;

  if (@_) 
  {
    $self->[$ROLL_OVER] = shift;
  }

  return($self->[$ROLL_OVER]);
}

# -------------------------------------------
sub minute_sf
{
  my $self = shift;

  if (@_) 
  {
    $self->[$MINUTE_SF] = shift;
  }

  return($self->[$MINUTE_SF]);
}

# -------------------------------------------
sub rrd_path
{
  my $self = shift;

  if (@_) 
  {
    $self->[$_RRD_PATH] = shift;
  }

  return($self->[$_RRD_PATH]);
}


# -------------------------------------------
sub id
{
  my $self = shift;

  return $self->SUPER::objid();
}
	   
sub last_run
{
  my $self = shift;

  return($self->[$_LAST_RUN]);
}

sub interval
{
  my $self = shift;

  return($self->[$_INTERVAL]);
}


# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------
# update
#   Generates a rrd file entry and gif with each invocation
#   Also updates html referencing the gif file.
#
#   NB Can be called at different intervals depending on the monitor.
# ----------------------------------------------------------------------------

sub update
{
  $DEBUG && TraceFuncs::trace(my $f);

  my $self = shift;
  my $ds = shift;

  my @names;
  my @values;
  my $rrd_id = parse_ds($ds, \@names, \@values);

  $self->[$DS]      = $ds;
  $self->[$_NAMES]  = \@names;
  $self->[$_VALUES] = \@values;

  #
  # We may want to record data at a slower rate than the rate
  # we do monitoring. See if our time is up to record a sample if not return.
  #
  my $current_step = ++ $self->[$_CURRENT_STEP];
  return if ( $current_step % $self->step() );

  #
  # Step intervals are measured in seconds so convert taking FTMON update
  # rate into account (which could vary slightly)
  #
  my $interval = 0;
  my $current_time  = time();
  if ( ! $self->[$_LAST_RUN] )
  {
    # need two iterations to calculate this value.
    $self->[$_LAST_RUN] = $current_time;
    return;
  }
  $interval =  $current_time - $self->[$_LAST_RUN];

  return if ( ! $interval );


  if ( $self->[$_INTERVAL] )
  {
    # If our scheduling changes too much then we have to blow away our graph
    # away and start again.
    if ( $interval  > 2 * $self->[$_INTERVAL] ) 
    {
      $self->[$_INTERVAL] = $interval;
      $self->[$_RRD_CREATED] = 0;
      unlink($self->[$_RRD_PATH]) || 
           die "Can not unlink " . $self->rrd_path() . " " . $!;

      $self->create_rrd($interval);
	   #die "monitoring interval varing too much - " . 
	   #$self->rrd_path() . " deleted";
	   return;
    }
  }
  else
  {
    $self->[$_INTERVAL] = $interval;
    $self->create_rrd($interval);
  }
  $self->[$_LAST_RUN] = $current_time;


  #
  # Store the data in the Round Robin database.
  #
  my $fields = "N";
  my $values = $self->[$_VALUES];
  foreach ( @$values )
  {
    $fields .= ":$_";
  }
 
  my $rrd_path = $self->rrd_path();
  $DEBUG && TraceFuncs::debug(
                   "RRDs::update(\n" .
		   "  $rrd_path,\n" .
		   "  $fields) )" );
  RRDs::update($rrd_path, $fields);
  if (RRDs::error)
  {
    unlink($rrd_path);
    die "RRDs::update(): Removing " . $self->rrd_file() . ": " . RRDs::error;
  }


  # We rollover the daily graphs on a new day boundary.
  $self->update_rollover();

  $self->update_graphs();
}


# ------------------------------------------------------------------------
sub create_rrd
{
  my $self = shift;
  my $step = shift;

  #
  # By default graph 3 days worth of data points in the rrd database.
  #
  my $graph_window = $self->[$_GRAPH_WINDOW]  = 
       ( defined($self->window()) && $self->window() )
               ?  ( $self->window() * $step )
               :  ( 3 * $DAY_MINUTES * $self->minute_sf() );

  my $heartbeat = 2 * $step;


  $DEBUG && TraceFuncs::debug( 
              "step = $step\n" .
              "day minutes = $DAY_MINUTES\n" .
              "minute sf = " . $self->minute_sf() . "\n" .
              "heartbeat = $heartbeat\n" .
              "graph_window = $graph_window" );


  #
  # Weekly - 30 minute average
  #
  $self->[$_RRD_WEEKLY_STEP] = 0;
  #if ( $step <  7 * $self->minute_sf() )
  if ( $step <  15 * $self->minute_sf() )
  {
    # How many steps make up a 30 minute averaging interval.
    $self->[$_RRD_WEEKLY_STEP] = FT::round( (30 * $self->minute_sf() / $step) );
  }

  #
  # Monthly - 2 hour average
  #
  $self->[$_RRD_MONTHLY_STEP] = 0;
  #if ( $step <  20 * 60 * $self->minute_sf() )
  if ( $step <  1 * 60 * $self->minute_sf() )
  {
      
    # How many steps make up 2 hour averaging interval.
    $self->[$_RRD_MONTHLY_STEP] = 
        FT::round( (2 * 60 * $self->minute_sf() / $step) );
  }


  my @rrd_ds = ();
  my $names = $self->[$_NAMES];
  foreach ( @$names )
  {
    push( @rrd_ds, "DS:" . $_ . ":GAUGE:" . $heartbeat .":U:U" );
  }


  #
  # Create the rrd file if it does not already exist.
  #
  my @rra;
  my $rrd_path = $self->rrd_path();
  if ( ! -e "$rrd_path" )
  {

    my $xff = 0.5;

    #
    # Define the Round Robin archive
    #
    if ( ! defined( $self->[$RRA] ) ||
         ! $self->[$RRA] )
    {
      #
      # Keep $graph_window worth of instantaneous values but must be at 
      # least WINDOW or MIN_SAMPLES;
      #
      my $samples = FT::round( ($graph_window / $step) );
      $samples = $self->min_samples()
             if ( $samples < $self->min_samples() );

      push( @rra,
            "RRA:LAST:" . $xff . ":" .
	    1 . ":" .
            $samples );
     
      $DEBUG && TraceFuncs::debug( "RRD: Keeping $samples " );
    }
    else
    {
      # REVISIT:
      @rra = split(",", $self->[$RRA]);
      foreach ( @rra )
      {
        $_ =~ s/^\s+//g;
        $_ =~ s/\s+$//g;
        $_ =~ s/^/RRA:/;
      }
    }

    # 600 samples of 5 minutes  (2 days and 2 hours)
    # 700 samples of 30 minutes (2 days and 2 hours, plus 12.5 days)
    # 775 samples of 2 hours    (above + 50 days)
    # 797 samples of 1 day      (above + 732 days, rounded up to 797)
    # rrdtool create myrouter.rrd         \
    #         DS:input:COUNTER:600:U:U   \
    #         DS:output:COUNTER:600:U:U  \
    #         RRA:AVERAGE:0.5:1:600      \
    #         RRA:AVERAGE:0.5:6:700      \
    #         RRA:AVERAGE:0.5:24:775     \
    #         RRA:AVERAGE:0.5:288:797    \
    #         RRA:MAX:0.5:1:600          \
    #         RRA:MAX:0.5:6:700          \
    #         RRA:MAX:0.5:24:775         \
    #         RRA:MAX:0.5:288:797


    if ( $self->[$_RRD_WEEKLY_STEP] )
    {
      
      push( @rra,
            "RRA:AVERAGE:" . $xff . ":" .
	    $self->[$_RRD_WEEKLY_STEP] . ":" .
            700 );
    }

    if ( $self->[$_RRD_MONTHLY_STEP] )
    {
      
      push( @rra,
            "RRA:AVERAGE:" . $xff . ":" .
	    $self->[$_RRD_MONTHLY_STEP] . ":" .
            775 );
    }


    #
    # Create the Round Robin file since it does not exist yet.
    #
    $DEBUG && TraceFuncs::debug(
                   "RRDs::create(\n" .
                   "   $rrd_path, \n" . 
                   "   --step $step,\n" .
                   "   ( @rrd_ds ),\n" .
                   "   ( @rra ) )" );


    RRDs::create( $rrd_path, "--step",  $step, @rrd_ds, @rra );
    if (RRDs::error)
    {
      die "RRDs::create() " . RRDs::error;
    }
    $self->[$_RRD_CREATED] = 1;
  }
}

# ---------------------------------------------------------------
sub update_graphs
{
  my $self = shift;

  #
  # Generate the instantaneous value graphs.
  #
  my $rrd_graph    = $self->rrd_file();
  my $rrd_path     = $self->rrd_path();
  my $interval     = $self->[$_INTERVAL];
  my $graph_window = $self->graph_window();

  my $names = $self->[$_NAMES]; 
  my $values = $self->[$_VALUES]; 


  $rrd_graph    =~ s/\.rrd$/\.gif/;

  my $curr_rrd_graph;
  my $curr_rrd_graph_path;
  my $ds_last;
  my $ds_line;

  my $sample_rate = FT::days2str( $interval / 60 / 60 / 24 );
  my $sample_interval = 
      FT::days2str( $graph_window / 60 / 60 / 24 );

  $i = 0;

  $rrd_path =~ s/:/\\:/g;
  my $title = $FT::PACKAGE . " " . $FT::RESOURCE;
  my $ds = $self->[$DS];
  foreach $graph ( @$ds )
  {
    my @html_graphs = ();
    $curr_rrd_graph = $rrd_graph;
    if ( ref($graph) eq "ARRAY" )
    {
      if ( @$graph == 2 )
      {
	my $graph_name  = $names->[$i] . "_" . $names->[$i + 1];
	$title = $FT::PACKAGE . " vs " . $FT::RESOURCE . " " . 
	         $names->[ $i ] . " " . $names->[$i + 1 ]; 

        $curr_rrd_graph =~ s/^/graph_/;
        $curr_rrd_graph =~ s/\.gif$/_$graph_name\.gif/;
        $curr_rrd_graph_path = 
	     $FTMON::Environment::SINGLETON->html_dir() . "/" .
	     $FT::VENDOR . "/" .
             $FT::PRODUCT .  "/" .
             $curr_rrd_graph;

        $self->generate_graph_2(
	     $sample_rate, 
	     $sample_interval, $graph_window,
	     $rrd_path, $curr_rrd_graph_path,
	     $names->[$i], $values->[$i],
	     $names->[$i + 1], $values->[$i + 1]);
	push(@html_graphs, $curr_rrd_graph);

        if ( $self->[$_ROLL_OVER_INDEX] != -1 )
        {
          my $roll_over_file = $curr_rrd_graph;
          $roll_over_file =~ s/\.(\w+)$/_rollover\.$1/;
          $self->generate_rollover_HTML(
            $roll_over_file,
            $FTMON::Environment::SINGLETON->html_dir() . "/" .
	      $FT::VENDOR . "/" .
              $FT::PRODUCT, $interval, $title );
        }
	     

        $curr_avg_rrd_graph = $curr_rrd_graph;
        $curr_avg_rrd_graph =~ s/\.gif$/_weekly_avg\.gif/;
        $curr_avg_rrd_graph_path = 
	    $FTMON::Environment::SINGLETON->html_dir() . "/" .
	    $FT::VENDOR . "/" .
            $FT::PRODUCT .  "/" .
            $curr_avg_rrd_graph;

	# Only do weekly graphs when averaging is complete.
        if ( $self->[$_RRD_WEEKLY_STEP] && 
             $self->[$_CURRENT_STEP]  > $self->[$_RRD_WEEKLY_STEP] &&
             ! ( $self->[$_CURRENT_STEP] % 
	         ( $self->[$_RRD_WEEKLY_STEP] * $self->step() ) ) )
	{

          $sample_rate = 
	    FT::days2str( $interval * $self->[$_RRD_WEEKLY_STEP] / 
		    60 / 60 / 24 );
	  $sample_window = ( 700 * $self->[$_RRD_WEEKLY_STEP] * $interval);
          $sample_interval = 
	    FT::days2str( $sample_window / 60 / 60 / 24 );

          $self->generate_weekly_graph_2(
	       $sample_rate, 
	       $sample_interval,
	       $sample_window,
	       $rrd_path, $curr_avg_rrd_graph_path,
	       $names->[$i], $names->[$i + 1] );
	  push(@html_graphs, $curr_avg_rrd_graph);
	}
	elsif ( -e $curr_avg_rrd_graph_path )
	{
	  push(@html_graphs, $curr_avg_rrd_graph);
	}


        $curr_avg_rrd_graph = $curr_rrd_graph;
        $curr_avg_rrd_graph =~ s/\.gif$/_monthly_avg\.gif/;
	$curr_avg_rrd_graph_path = 
	   $FTMON::Environment::SINGLETON->html_dir() . "/" .
	   $FT::VENDOR . "/" .
	   $FT::PRODUCT .  "/" .
	   $curr_avg_rrd_graph;

        if ( $self->[$_RRD_MONTHLY_STEP] &&
	     $self->[$_CURRENT_STEP]  > $self->[$_RRD_MONTHLY_STEP] &&
             ! ( $self->[$_CURRENT_STEP] % 
	         ( $self->[$_RRD_MONTHLY_STEP] * $self->step()) ) )
	{

          $sample_rate = FT::days2str( 
		  $interval * $self->[$_RRD_MONTHLY_STEP] /
		  60 / 60 / 24 );
	  $sample_window = ( 775 * $self->[$_RRD_MONTHLY_STEP] * $interval);
          $sample_interval = 
	    FT::days2str( $sample_window / 60 / 60 / 24 );

          $self->generate_monthly_graph_2(
	       $sample_rate, 
	       $sample_interval,
	       $sample_window,
	       $rrd_path, $curr_avg_rrd_graph_path,
	       $names->[$i], $names->[$i + 1] );
	  push(@html_graphs, $curr_avg_rrd_graph);
	}
	elsif ( -e $curr_avg_rrd_graph_path )
	{
	  push(@html_graphs, $curr_avg_rrd_graph);
	}


        $self->generate_HTML(
           $FTMON::Environment::SINGLETON->html_dir() . "/" . 
	     $FT::VENDOR . "/" .
	     $FT::PRODUCT, 
	   \@html_graphs,
	   $interval, $title);

	$i += 2;
      }
      else
      {
        die "There is currently a limit of 2 values plotted per graph";
      }
    }
    else
    {
      my $graph_name  = $names->[$i];
      $title = $FT::PACKAGE . " " . $FT::RESOURCE . " " . $names->[ $i ];

      $curr_rrd_graph =~ s/^/graph_/;
      $curr_rrd_graph =~ s/\.gif$/_${graph_name}\.gif/;
      $curr_rrd_graph_path = $FTMON::Environment::SINGLETON->html_dir() . "/" .
			     $FT::VENDOR . "/" .
                             $FT::PRODUCT . "/" .
                             $curr_rrd_graph;
      $self->generate_graph_1(
	    $sample_rate, $sample_interval, $graph_window,
            $rrd_path, $curr_rrd_graph_path,
	    $graph_name, $values->[$i]);

      push(@html_graphs, $curr_rrd_graph);

      if ( $self->[$_ROLL_OVER_INDEX] != -1 )
      {
        my $roll_over_file = $curr_rrd_graph;
        $roll_over_file =~ s/\.(\w+)$/_rollover\.$1/;
        $self->generate_rollover_HTML(
          $roll_over_file,
          $FTMON::Environment::SINGLETON->html_dir() . "/" .
	    $FT::VENDOR . "/" .
            $FT::PRODUCT, $interval, $title );
      }


      $curr_avg_rrd_graph = $curr_rrd_graph;
      $curr_avg_rrd_graph =~ s/\.gif$/_weekly_avg\.gif/;
      $curr_avg_rrd_graph_path = 
          $FTMON::Environment::SINGLETON->html_dir() . "/" .
          $FT::VENDOR . "/" .
          $FT::PRODUCT . "/" .
          $curr_avg_rrd_graph;
      if ( $self->[$_RRD_WEEKLY_STEP] && 
	     $self->[$_CURRENT_STEP]  > $self->[$_RRD_WEEKLY_STEP] &&
           ! ( $self->[$_CURRENT_STEP] % 
	       ( $self->[$_RRD_WEEKLY_STEP] * $self->step() ) ) 
         )
      {

        $sample_rate = FT::days2str( 
               $interval * $self->[$_RRD_WEEKLY_STEP] / 
	          60 / 60 / 24 );
        $sample_window = ( 700 * $self->[$_RRD_WEEKLY_STEP] * $interval);
        $sample_interval = 
	   FT::days2str( $sample_window / 60 / 60 / 24 );

        $self->generate_weekly_graph_1(
	       $sample_rate, 
	       $sample_interval,
	       $sample_window,
	       $rrd_path, $curr_avg_rrd_graph_path,
	       $graph_name );
	       
        push(@html_graphs, $curr_avg_rrd_graph);
      }
      elsif ( -e $curr_avg_rrd_graph_path )
      {
        push(@html_graphs, $curr_avg_rrd_graph);
      }

      $curr_avg_rrd_graph = $curr_rrd_graph;
      $curr_avg_rrd_graph =~ s/\.gif$/_monthly_avg\.gif/;
      $curr_avg_rrd_graph_path = 
          $FTMON::Environment::SINGLETON->html_dir() . "/" .
          $FT::VENDOR . "/" .
          $FT::PRODUCT . "/" .
          $curr_avg_rrd_graph;
      if ( $self->[$_RRD_MONTHLY_STEP] &&
	   $self->[$_CURRENT_STEP]  > $self->[$_RRD_MONTHLY_STEP] &&
           ! ( $self->[$_CURRENT_STEP] % 
	       ( $self->[$_RRD_MONTHLY_STEP] * $self->step()) ) 
         )
      {

        $sample_rate = 
	  FT::days2str( $interval * $self->[$_RRD_MONTHLY_STEP] /
		  60 / 60 / 24 );
        $sample_window = ( 775 * $self->[$_RRD_MONTHLY_STEP] * $interval);
        $sample_interval = 
	  FT::days2str( $sample_window / 60 / 60 / 24 );

        $self->generate_monthly_graph_1(
	       $sample_rate, 
	       $sample_interval,
	       $sample_window,
	       $rrd_path, $curr_avg_rrd_graph_path,
	       $graph_name );

        push(@html_graphs, $curr_avg_rrd_graph);

      }
      elsif ( -e $curr_avg_rrd_graph_path )
      {
        push(@html_graphs, $curr_avg_rrd_graph);
      }


      $self->generate_HTML(
          $FTMON::Environment::SINGLETON->html_dir() . "/" .
	    $FT::VENDOR . "/" .
            $FT::PRODUCT,
	  \@html_graphs, 
	  $interval, $title );

      $i++;
    }
  }
}

# --------------------------------------------------------
sub update_rollover
{
  my $self = shift;

  my $current_time  = time();

  my $dummy;
  my $current_yday;
  my $next_yday;

  ( $dummy,  $dummy,  $dummy, $dummy, $dummy,
      $dummy, $dummy, $current_yday, $dummy )
             = localtime( $current_time );

  ( $dummy,  $dummy,  $dummy, $dummy, $dummy,
      $dummy, $dummy, $next_yday, $dummy )
             = localtime( $current_time + 1.5 * $self->[$_INTERVAL]  );

  # Its a new day if the next interval will span accross to the next day.
  my $new_day = ( $current_yday != $next_yday );


  $self->[$_ROLL_OVER_INDEX] = -1;
  my $roll_over = $self->roll_over() - 1 if ( $self->roll_over() > 1 );
  if ( $new_day && $roll_over  )
  {
    if ( defined($self->[$_CURRENT_ROLL_OVER]) &&
         $self->[$_CURRENT_ROLL_OVER] < $roll_over )
    {
      $self->[$_CURRENT_ROLL_OVER]++;
    }
    else
    {
      $self->[$_CURRENT_ROLL_OVER] = 0;
    }
    $self->[$_ROLL_OVER_INDEX] = $self->[$_CURRENT_ROLL_OVER];
  }
}


# -----------------
# rrdtool graph myrouter-day.gif --start -86400 \
#     DEF:inoctets=myrouter.rrd:input:AVERAGE \
#     DEF:outoctets=myrouter.rrd:output:AVERAGE \
#     AREA:inoctets#00FF00:"In traffic" \
#     LINE1:outoctets#0000FF:"Out traffic"
sub generate_graph_1
{
  my $self = shift;

  my $sample_rate = shift;
  my $sample_interval = shift;
  my $graph_window = shift;
  my $rrd_path = shift;
  my $curr_rrd_graph_path = shift;
  my $name = shift;
  my $value = shift;

  my $label = $name;
  $label = $label .  " (" . $FT::COMMENT{$name} . ")" 
                   if ( defined($FT::COMMENT{$name}) );

  $value = FT::round($value, 2);
  $ds_last = "DEF:" .   $name . "=" . $rrd_path . ":" . $name . ":LAST";
  $ds_line = "LINE2:" . $name .  "#FF0000" . ":" .
             $value .  "  " . $label;

  $DEBUG && TraceFuncs::debug( "RRDs::graph(\n" .
                  "  $curr_rrd_graph_path,\n" .
                  "  -s -" . $graph_window . "s,\n" .
                  "  $ds_last,\n" .
		  "  $ds_line )" );

  RRDs::graph(
        "--title", 
	     "  $FT::PACKAGE $FT::RESOURCE",
        $curr_rrd_graph_path,
        "-s -" . $graph_window . "s",
	"-w", $self->graph_width(), "-h", $self->graph_height(),
        $ds_last, $ds_line,
        "COMMENT:\\n",
        "COMMENT:" .
	     "  SampleRate = $sample_rate," .
	     "  LastUpdate = " .
             $FT::Day . " " .
             $FT::dd . " " .
	     $FT::Mth . " " .
	     $FT::yyyy . " " .
	     $FT::hh . ":" .
	     $FT::mm . ":" .
	     $FT::ss . ", ",
        "COMMENT:\\n",
        "COMMENT:" .
	     "  Window = $sample_interval");

  if (RRDs::error)
  {
    die "RRDs::graph() " . RRDs::error;
  }

  my $roll_over_index = $self->[$_ROLL_OVER_INDEX];
  if ( $self->[$_ROLL_OVER_INDEX] != -1 )
  {

    $curr_rrd_graph_path =~ s/\.(\w+)$/_rollover_${roll_over_index}\.$1/;

    RRDs::graph(
        "--title", 
	     "  $FT::PACKAGE $FT::RESOURCE",
        $curr_rrd_graph_path,
	"-w", $self->graph_width(), "-h", $self->graph_height(),
        $ds_last, $ds_line,
        "COMMENT:\\n",
        "COMMENT:" .
	     "  SampleRate = $sample_rate," .
	     "  LastUpdate = " .
             $FT::Day . " " .
             $FT::dd . " " .
	     $FT::Mth . " " .
	     $FT::yyyy . " " .
	     $FT::hh . ":" .
	     $FT::mm . ":" .
	     $FT::ss . ", ",
        "COMMENT:\\n",
        "COMMENT:" .
	     "  Window = 1 day\\n");

    if (RRDs::error)
    {
      die "RRDs::graph() " . RRDs::error;
    }
  }

}

# -----------------
sub generate_graph_2
{
  my $self = shift;

  my $sample_rate = shift;
  my $sample_interval = shift;
  my $graph_window = shift;
  my $rrd_path = shift;
  my $curr_rrd_graph_path = shift;
  my $name1 = shift;
  my $value1 = shift;
  my $name2 = shift;
  my $value2 = shift;

  my $label1 = $name1;
  $label1 = $label1 .  " (" . $FT::COMMENT{$name1} . ")" 
                   if ( defined($FT::COMMENT{$name1}) );

  $ds_last1 = "DEF:" .   $name1 . "=" . $rrd_path . ":" . $name1 . ":LAST";
  $ds_last2 = "DEF:" .   $name2 . "=" . $rrd_path . ":" . $name2 . ":LAST";

  $value1 = FT::round($value1, 2);
  $ds_line1 = "AREA:" . $name1 .  "#00FF00" . ":" .
             $value1 .  "  " . $label1;

  my $label2 = $name2;
  $label2 = $label2 .  " (" . $FT::COMMENT{$name2} . ")" 
                   if ( defined($FT::COMMENT{$name2}) );

  $value2 = FT::round($value2, 2);
  $ds_line2 = "LINE1:" . $name2 .  "#0000FF" . ":" .
             $value2 . "  " . $label2;


  $DEBUG && TraceFuncs::debug( "RRDs::graph(\n" .
                  "  $curr_rrd_graph_path,\n" .
                  "  -s -" . $graph_window . "s,\n" .
                  "  $ds_last1,\n" .
                  "  $ds_last2,\n" .
		  "  $ds_line1,\n",
		  "  $ds_line2)" );
  RRDs::graph(
        "--title", 
	     "  $FT::PACKAGE $FT::RESOURCE",
        $curr_rrd_graph_path,
        "-s -" . $graph_window . "s",
	"-w", $self->graph_width(), "-h", $self->graph_height(),
        $ds_last1, $ds_last2, $ds_line1, "COMMENT:\\n", $ds_line2,
        "COMMENT:\\n",
        "COMMENT:" .
	     "  SampleRate = $sample_rate," .
	     "  LastUpdate = " .
             $FT::Day . " " .
             $FT::dd . " " .
	     $FT::Mth . " " .
	     $FT::yyyy . " " .
	     $FT::hh . ":" .
	     $FT::mm . ":" .
	     $FT::ss . ",",
        "COMMENT:\\n",
	"COMMENT:  Window = $sample_interval");

  if (RRDs::error)
  {
    die "RRDs::graph() " . RRDs::error;
  }

  my $roll_over_index = $self->[$_ROLL_OVER_INDEX];
  if ( $self->[$_ROLL_OVER_INDEX] != -1 )
  {
    $curr_rrd_graph_path =~ s/\.(\w+)$/_rollover_${roll_over_index}\.$1/;


    RRDs::graph(
        "--title", 
	     "  $FT::PACKAGE $FT::RESOURCE",
        $curr_rrd_graph_path,
	"-w", $self->graph_width(), "-h", $self->graph_height(),
        $ds_last1, $ds_last2, $ds_line1, "COMMENT:\\n", $ds_line2,
        "COMMENT:\\n",
        "COMMENT:" .
	     "  SampleRate = $sample_rate," .
	     "  LastUpdate = " .
             $FT::Day . " " .
             $FT::dd . " " .
	     $FT::Mth . " " .
	     $FT::yyyy . " " .
	     $FT::hh . ":" .
	     $FT::mm . ":" .
	     $FT::ss . ", ",
        "COMMENT:\\n",
	"COMMENT:  Window = 1 day");

    if (RRDs::error)
    {
      die "RRDs::graph() " . RRDs::error;
    }
  }


}

# -----------------
sub generate_weekly_graph_2
{
  my $self = shift;

  my $sample_rate = shift;
  my $sample_interval = shift;
  my $graph_window = shift;
  my $rrd_path = shift;
  my $curr_rrd_graph_path = shift;
  my $name1 = shift;
  my $name2 = shift;

  my $label1 = $name1;
  $label1 = $label1 .  " (" . $FT::COMMENT{$name1} . ")" 
                   if ( defined($FT::COMMENT{$name1}) );

  $ds_last1 = "DEF:" .   $name1 . "=" . $rrd_path . ":" . $name1 . ":AVERAGE";
  $ds_last2 = "DEF:" .   $name2 . "=" . $rrd_path . ":" . $name2 . ":AVERAGE";

  $ds_line1 = "AREA:" . $name1 .  "#00FF00" . ":" .  $label1;

  my $label2 = $name2;
  $label2 = $label2 .  " (" . $FT::COMMENT{$name2} . ")" 
                   if ( defined($FT::COMMENT{$name2}) );

  $ds_line2 = "LINE1:" . $name2 .  "#0000FF" . ":" .  $label2;


  $DEBUG && TraceFuncs::debug( "RRDs::graph(\n" .
                  "  $curr_rrd_graph_path,\n" .
                  "  -s -" . $graph_window . ")" );
  RRDs::graph(
        "--title", 
	     "  $FT::PACKAGE $FT::RESOURCE",
        $curr_rrd_graph_path,
        "-s -" . $graph_window . "s",
	"-w", $self->graph_width(), "-h", $self->graph_height(),
        $ds_last1, $ds_last2, $ds_line1, "COMMENT:\\n", $ds_line2,
        "COMMENT:\\n",
        "COMMENT:" .
	     "  Weekly Graph (30 minute average)",
        "COMMENT:\\n",
        "COMMENT:" .
	     "  SampleRate = $sample_rate," .
	     "  LastUpdate = " .
             $FT::Day . " " .
             $FT::dd . " " .
	     $FT::Mth . " " .
	     $FT::yyyy . " " .
	     $FT::hh . ":" .
	     $FT::mm . ":" .
	     $FT::ss . ", ",
        "COMMENT:\\n",
        "COMMENT:" .
	     "  Window = $sample_interval");

  if (RRDs::error)
  {
    die "RRDs::graph() $curr_rrd_graph_path: " .
        "$sample_rate : $sample_interval : $graph_window " . RRDs::error;
  }

}

# -----------------------------------------------------------------------------
sub generate_monthly_graph_2
{
  my $self = shift;

  my $sample_rate = shift;
  my $sample_interval = shift;
  my $graph_window = shift;
  my $rrd_path = shift;
  my $curr_rrd_graph_path = shift;
  my $name1 = shift;
  my $name2 = shift;

  my $label1 = $name1;
  $label1 = $label1 .  " (" . $FT::COMMENT{$name1} . ")" 
                   if ( defined($FT::COMMENT{$name1}) );

  $ds_last1 = "DEF:" .   "dummy1" . "=" . $rrd_path . ":" . $name1 . ":AVERAGE";
  $ds_last2 = "DEF:" .   "dummy2" . "=" . $rrd_path . ":" . $name2 . ":AVERAGE";
  $ds_last3 = "DEF:" .   $name1 . "=" . $rrd_path . ":" . $name1 . ":AVERAGE";
  $ds_last4 = "DEF:" .   $name2 . "=" . $rrd_path . ":" . $name2 . ":AVERAGE";

  $ds_line1 = "AREA:" . $name1 .  "#00FF00" . ":" .
             $label1;

  my $label2 = $name2;
  $label2 = $label2 .  " (" . $FT::COMMENT{$name2} . ")" 
                   if ( defined($FT::COMMENT{$name2}) );

  $ds_line2 = "LINE1:" . $name2 .  "#0000FF" . ":" .  $label2;


  $DEBUG && TraceFuncs::debug( "RRDs::graph(\n" .
                  "  $curr_rrd_graph_path,\n" .
                  "  -s -" . $graph_window . ")" );
  RRDs::graph(
        "--title", 
	     "  $FT::PACKAGE $FT::RESOURCE",
        $curr_rrd_graph_path,
        "-s -" . $graph_window . "s",
	"-w", $self->graph_width(), "-h", $self->graph_height(),
        $ds_last1, $ds_last2, $ds_last3, $ds_last4, 
	$ds_line1, "COMMENT:\\n", $ds_line2,
        "COMMENT:\\n",
        "COMMENT:" .
	     "  Monthly Graph (2 hour average)",
        "COMMENT:\\n",
        "COMMENT:" .
	     "  SampleRate = $sample_rate," .
	     "  LastUpdate = " .
             $FT::Day . " " .
             $FT::dd . " " .
	     $FT::Mth . " " .
	     $FT::yyyy . " " .
	     $FT::hh . ":" .
	     $FT::mm . ":" .
	     $FT::ss . ", ",
        "COMMENT:\\n",
        "COMMENT:" .
	     "  Window = $sample_interval");

  if (RRDs::error)
  {
    die "RRDs::graph() $curr_rrd_graph_path: " . RRDs::error;
  }

}

# -----------------------------------------------------------------------------
sub generate_weekly_graph_1
{
  my $self = shift;

  my $sample_rate = shift;
  my $sample_interval = shift;
  my $graph_window = shift;
  my $rrd_path = shift;
  my $curr_rrd_graph_path = shift;
  my $name1 = shift;

  my $label1 = $name1;
  $label1 = $label1 .  " (" . $FT::COMMENT{$name1} . ")" 
                   if ( defined($FT::COMMENT{$name1}) );

  $ds_last1 = "DEF:" .   $name1 . "=" . $rrd_path . ":" . $name1 . ":AVERAGE";

  $label1 = $name1;
  $label1 = $label1 .  " (" . $FT::COMMENT{$name1} . ")" 
                   if ( defined($FT::COMMENT{$name1}) );

  $ds_line1 = "LINE1:" . $name1 .  "#0000FF" . ":" .  $label1;


  $DEBUG && TraceFuncs::debug( "RRDs::graph(\n" .
                  "  $curr_rrd_graph_path,\n" .
                  "  -s -" . $graph_window . ")" );
  RRDs::graph(
        "--title", 
	     "  $FT::PACKAGE $FT::RESOURCE",
        $curr_rrd_graph_path,
        "-s -" . $graph_window . "s",
	"-w", $self->graph_width(), "-h", $self->graph_height(),
        $ds_last1, $ds_line1,
        "COMMENT:\\n",
        "COMMENT:" .
	     "  Weekly Graph (30 minute average)" .
	     "  SampleRate = $sample_rate," .
	     "  LastUpdate = " .
             $FT::Day . " " .
             $FT::dd . " " .
	     $FT::Mth . " " .
	     $FT::yyyy . " " .
	     $FT::hh . ":" .
	     $FT::mm . ":" .
	     $FT::ss . ", ",
        "COMMENT:\\n",
        "COMMENT:" .
	     "  Window = $sample_interval\\n");

  if (RRDs::error)
  {
    die "RRDs::graph() $curr_rrd_graph_path: " .
        "$sample_rate : $sample_interval : $graph_window " . RRDs::error;
  }

}

# -----------------------------------------------------------------------------
sub generate_monthly_graph_1
{
  my $self = shift;

  my $sample_rate = shift;
  my $sample_interval = shift;
  my $graph_window = shift;
  my $rrd_path = shift;
  my $curr_rrd_graph_path = shift;
  my $name1 = shift;

  my $label1 = $name1;
  $label1 = $label1 .  " (" . $FT::COMMENT{$name1} . ")" 
                   if ( defined($FT::COMMENT{$name1}) );

  $ds_last1 = "DEF:" .   "dummy1" . "=" . $rrd_path . ":" . $name1 . ":AVERAGE";
  $ds_last2 = "DEF:" .   $name1 . "=" . $rrd_path . ":" . $name1 . ":AVERAGE";


  $label1 = $name1;
  $label1 = $label1 .  " (" . $FT::COMMENT{$name1} . ")" 
                   if ( defined($FT::COMMENT{$name1}) );

  $ds_line2 = "LINE1:" . $name1 .  "#0000FF" . ":" .  $label1;


  $DEBUG && TraceFuncs::debug( "RRDs::graph(\n" .
                  "  $curr_rrd_graph_path,\n" .
                  "  -s -" . $graph_window . ")" );
  RRDs::graph(
        "--title", 
	     "  $FT::PACKAGE $FT::RESOURCE",
        $curr_rrd_graph_path,
        "-s -" . $graph_window . "s",
	"-w", $self->graph_width(), "-h", $self->graph_height(),
        $ds_last1, $ds_last2, $ds_line1,
        "COMMENT:\\n",
        "COMMENT:" .
	     "  Monthly Graph (2 hour average)" .
	     "  SampleRate = $sample_rate," .
	     "  LastUpdate = " .
             $FT::Day . " " .
             $FT::dd . " " .
	     $FT::Mth . " " .
	     $FT::yyyy . " " .
	     $FT::hh . ":" .
	     $FT::mm . ":" .
	     $FT::ss . ", ",
        "COMMENT:\\n",
        "COMMENT:" .
	     "  Window = $sample_interval\\n");

  if (RRDs::error)
  {
    die "RRDs::graph() $curr_rrd_graph_path: " . RRDs::error;
  }

}





# ----------------------------------------------------------------------------
# generate_HTML
#     Generates a html wrapper for ${l_gif_file} with a refresh rate of
#     ${l_step}. The wrapper is placed in ${l_rrd_dir}.
#
#     It also updates the index.html file with this new entry.
# ----------------------------------------------------------------------------
sub generate_HTML
{
  my $self = shift;

  my $rrd_dir = shift;
  my $gif_file = shift;
  my $step = shift;
  my $title = shift;

  my $generate_index = 0;


  $DEBUG && TraceFuncs::trace(my $f);

  # $gif_file = substr($gif_file, rindex($gif_file, "/") + 1);

  my $html_file = $gif_file->[0];
  $html_file =~ s/\.gif$/\.html/;
  $html_file = "unknown.html" if ( ! $html_file );

  my $html_file_path  = $rrd_dir . "/" . $html_file;
  $DEBUG && TraceFuncs::debug(
                   "html  = $html_file_path\n" .
                   "title = $title" );

  if ( ! open( HTML, "> $html_file_path" ) )
  {
    sleep(1);
    open( HTML, "> $html_file_path" )  ||
        die __LINE__ . ": Could not generate $html_file_path : $!";
  }

  print HTML 
        "<html>\n<head>\n<title>$title</title>\n</head>\n" .
        "<meta HTTP-EQUIV=\"Refresh\" CONTENT=\"$step\">\n" .
        "<body>\n" .
        "<center><h1>$title</h1></center>\n";

	#"WIDTH=\"750\" " .
	#"HEIGHT=\"160\">\n" .
	
  my $roll_over_file = $html_file;
  $roll_over_file =~ s/\.(\w+)$/_rollover\.$1/;

  my $roll_over_file_path = $rrd_dir . "/" . $roll_over_file;

  print HTML "<A HREF=\"$roll_over_file\">[daily history]</A><br>\n"
     if ( -f $roll_over_file_path );

  foreach $gif_file ( @{$gif_file} )
  {
    my $gif_file_path   = $rrd_dir . "/" . $gif_file;
    $DEBUG && TraceFuncs::debug("gif = $gif_file_path" );

    print HTML "<IMG SRC=\"$gif_file\"BORDER=\"0\">\n<br>\n";
    #"WIDTH=\"750\" " .
    #"HEIGHT=\"160\">\n" .

  }

  print HTML "</body>\n</html>\n";
  close( HTML ) || die "Error closing $html_file_path : $!";

  $self->add_graph($title, "./$html_file");
}

# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------
sub add_graph 
{
  my $self = shift;

  my $title = shift;
  my $html_file = shift;

  #
  # Don't add the same graph twice. 
  # REVISIT: This is messy - find a better way.
  #
  my $graphs = $self->[$_GRAPHS];
  push( @{$graphs}, [ $title, "./" . $html_file ] )
     if ( ! grep { $_->[0] eq $title } @{$graphs} );
}

# ----------------------------------------------------------------------------
sub list_graphs 
{
  my $self = shift;

  return $self->[$_GRAPHS];
}


# ----------------------------------------------------------------------------
# RRD_IMPL::generate_rollover_HTML
# ----------------------------------------------------------------------------
sub generate_rollover_HTML
{
  my $self = shift;

  my $gif_file = shift;
  my $html_dir = shift;
  my $step = shift;
  my $title = shift;

  my $generate_index = 0;


  $DEBUG && TraceFuncs::trace(my $f);

  # $gif_file = substr($gif_file, rindex($gif_file, "/") + 1);

  my $html_file = $gif_file;
  $html_file =~ s/\.(\w+)$/\.html/;

  my $html_file_path  = $html_dir . "/" . $html_file;

  $DEBUG && TraceFuncs::debug(
                   "html  = $html_file_path\n" .
                   "title = $title" );

  open( HTML, "> $html_file_path" ) ||
        die __LINE__ . ": Could not generate $html_file_path : $!";

  print HTML 
        "<html>\n<head>\n<title>$title</title>\n</head>\n" .
        "<meta HTTP-EQUIV=\"Refresh\" CONTENT=\"$step\">\n" .
        "<body>\n" .
        "<center><h1>$title</h1></center>\n";

	#"WIDTH=\"750\" " .
	#"HEIGHT=\"160\">\n" .
	#
  opendir(ROVER, $html_dir) || die "Could not open '$html_dir' : $!";

  my $rollover;
  my $file_prefix = $html_file;
  $file_prefix =~ s/\.html$//;

  foreach $rollover ( readdir(ROVER) )
  {
    next if ( $rollover !~ /${file_prefix}_\d+\.gif/ );
    chomp;
  
    $DEBUG && TraceFuncs::debug("gif = $rollover" );

    print HTML "<IMG SRC=\"$rollover\"BORDER=\"0\">\n<br>\n";
    #"WIDTH=\"750\" " .
    #"HEIGHT=\"160\">\n" .

  }
  closedir( ROVER ) || die "Error closing $html_dir : $!";


  print HTML "</body>\n</html>\n";
  close( HTML ) || die "Error closing $html_file_path : $!";

  #
  # Don't add the same graph twice.
  #
  #push( @{$FT::GRAPHS{$FT::PACKAGE}}, [ $title, "./" . $html_file ] )
  #   if ( ! grep { $_->[0] eq $title } @{$FT::GRAPHS{$FT::PACKAGE}} );
}


  # ----------------------------------------------
  sub FT::rrd
  {
    my $ds = shift;
    my $param = shift;
    my $rrd = FTMON::RRD->new($ds, $param);
    $rrd->update($ds);
  }


#
# C:\ftmon\Base\lib\FTMON>perl \
#     -I ../MSWin32 -I ../Auto/rrds -I .. \
#     c:\ftmon\base\lib\ftmon\rrd.pm
# 
if ( defined($ENV{'TEST_HARNESS'}) )
{
  $FT::PACKAGE    = "fred";
  $FT::HTML_DIR = "/temp";

  $field1 = 0;
  $field2 = 0;
  $field3 = 0;
  $VALUES = [ [ *field1, *field2 ], *field3 ];

  $FT::COMMENT{field1} = "This is a comment for field1";
  $FT::COMMENT{field3} = "This is a comment for field3";
  my $x;
  for ( $x = 0; $x <= 360; $x++ )
  {
    $field1 = cos($x/10);
    $field2 = sin($x/10);
    $field3 = rand(40);
    print $x, " ", $field1, " ", $field2, " ", $field3, "\n";

    $VALUES = [ [ *field1, *field2 ], *field3 ];

    #FT::rrd($VALUES, { RRD_DIR => "/temp", MINUTE_SF => 2} );
    FT::rrd($VALUES, { RRD_DIR => "/temp"} );

    sleep(2);
  }
  exit(0);
}


1;
