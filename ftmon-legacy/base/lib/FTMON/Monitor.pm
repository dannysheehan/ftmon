package FTMON::Monitor;
############################################################################
#                    FTMON (Fast Track Systems Monitor)
#
#   Script: @(#) $RCSfile: Monitor.pm,v $ 
# 
#   DESCRIPTION: 
#
#   @(#) Periodically scheduled script that returns values that are checked
#   @(#) against user defined thresholds.
#
#   $Source: /cvsroot/ftmon/base2/lib/FTMON/Monitor.pm,v $
#
#   $Date: 2003/04/20 12:36:13 $
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
#      PO Box 238
#      Eastwood NSW 2122
#      AUSTRALIA
#      dsheehan@ftmon.org
#      http://ftmon.org
#
#############################################################################
use TraceFuncs;
use FTMON::Base;
use FTMON::Calculation;
use FTMON::Environment;
use FTMON::Scheduler;

# FT::EVENT_MGR
#     - global variable that must be defined to identify the
#     event manager to forward events to.
#
# Each time a monitor runs the following global variables are set.
#
#  FT::MONITOR::RUN_TIME
#     - time taken by monitor to run.
#
#  FT::MONITOR::LAST_RUN
#     - actual time that monitor last time.
#
#  FT::MONITOR::BASELINED
#     - indicates time monitor was lased baselined.
#
#  FT::VENDOR
#     - vendor of product being monitored.
#
#  FT::PRODUCT
#     - product being monitored.
#
#  FT::MONITOR::NAME
#     - name of monitor monitoring product
#
#  FT::PRODUCT::SUMMARY
#
#  FT::PRODUCT::DESC
#
#  FT::PRODUCT::CONTACT
#
#  FT::MONITOR::DESC
#
#  FT::MONITOR::VER
#
#  FT::PACKAGE
#     - package name of monitor.
#
#  FT::BASE_I 
#     - $FT::VENDOR . "/" . $FT::PRODUCT . "/" . $FT::MONITOR::NAME;
# FT::MONITOR::PERSIST_INFO
# FT::MONITOR::POST_INFO
# FT::MONITOR::INFO


  $DEBUG = 0 if ( ! defined($FTMON::Monitor::DEBUG) );

  @FTMON::Monitor::ISA = ("FTMON::Base");

  my %MonitorList = ();

  $_LAST_ATTRIB = $FTMON::Base::_LAST_ATTRIB + 24;
  my($CONFIG_FILE,
     $NAME,
     $VENDOR,
     $PRODUCT,
     $MONITOR,
     $DESCRIPTION,
     $VERSION,
     $SUBR_COMPILED,
     $ESUBR_COMPILED,
     $PRECALC_SUBR,
     $THRESHOLD_SUBR,
     $ENVIRONMENT_SUBR,
     $VARIABLES_SUBR,
     $VARIABLES_IMPL_SUBR,
     $ESCULATION_SUBR,
     $ASSIGN_ROW_SUBR,
     $SCHED_SUBR,
     $COLS,
     $INTERVAL,
     $LAST_RUN,
     $RUN_TIME,
     $HIGHEST_SEV,
     $BASELINED,
     $STATE,
    ) = ( $FTMON::Base::_LAST_ATTRIB + 1 .. $_LAST_ATTRIB );


  # -------------------------------------------------------------------------
  sub new
  {
    $DEBUG && TraceFuncs::trace(my $f);
    my $proto = shift;
    my $name = shift;
    my $config_file = shift;
    my $description = shift;
    my $version = shift;

    my $class = ref($proto) || $proto;
    
    my $self = $class->SUPER::new($name);

    my $monitor = [];
    if ( $monitor = 
           $FTMON::ConfigFileParser::SINGLETON->find_monitor_instance($name) )
    {
      return($monitor);
    }

    $self->[$CONFIG_FILE] = ( defined($config_file) ) ? $config_file : undef;
    $self->[$SUBR_COMPILED] = undef;
    $self->[$ESUBR_COMPILED] = undef;

    $self->[$PRECALC_SUBR] = undef;
    $self->[$THRESHOLD_SUBR] = undef;
    $self->[$ENVIRONMENT_SUBR] = undef;
    $self->[$VARIABLES_SUBR] = undef;
    $self->[$VARIABLES_IMPL_SUBR] = undef;
    $self->[$ESCULATION_SUBR] = undef;
    $self->[$ASSIGN_ROW_SUBR] = undef;
    $self->[$SCHED_SUBR] = undef;
    $self->[$COLS] = undef;
    $self->[$LAST_RUN] = 0;
    $self->[$RUN_TIME] = 0;
    $self->[$BASELINED] = 0;
    $self->[$STATE] = 1;

    bless($self, $class);

    $self->[$DESCRIPTION] = "unknown";
    $self->[$DESCRIPTION] = $description if ( defined $description );

    $self->[$VERSION] = "unknown";
    $self->[$VERSION] = $version if ( defined $version );

    $self->name($name);
    my $vendor;
    my $product;
    my $monitor_name;
    ($vendor, $product, $monitor_name) = split("::", $name);
    die "Monitor name must be of form <vendor>::<product>::<monitor> for $name"
      if ( ! defined($vendor) ||
           ! defined($product) ||
           ! defined($monitor_name) );

    $self->vendor_name($vendor);
    $self->product_name($product);
    $self->monitor_name($monitor_name);

    return($self);
  
  }


  # ----------------------------------------------------------------------
  sub DESTROY
  {
    my $self  = shift;
    $self->SUPER::DESTROY();
  }


  # -------------------------------------------------------------------------
  sub description
  {
    my $self = shift;
    if (@_) 
    {
      $self->[$DESCRIPTION] = shift;
    }
    return ( $self->[$DESCRIPTION] );
  }

  # -------------------------------------------------------------------------
  sub enable
  {
    my $self = shift;
    $self->[$STATE] = 1;
  }

  # -------------------------------------------------------------------------
  sub disable
  {
    my $self = shift;
    $self->[$STATE] = 0;
  }



  # -------------------------------------------------------------------------
  sub version
  {
    my $self = shift;

    if (@_) 
    {
      $self->[$VERSION] = shift;
    }

    return ( $self->[$VERSION] );
  }

  # -------------------------------------------------------------------------
  sub baselined
  {
    my $self = shift;

    if (@_) 
    {
      $self->[$BASELINED] = shift;
    }

    return($self->[$BASELINED]);
  }

  # -------------------------------------------------------------------------
  sub vendor_name
  {
    my $self = shift;

    if (@_) 
    {
      $self->[$VENDOR] = shift;
    }

    return($self->[$VENDOR]);
  }

  # -------------------------------------------------------------------------
  sub monitor_name
  {
    my $self = shift;

    if (@_) 
    {
      $self->[$MONITOR] = shift;
    }

    return($self->[$MONITOR]);
  }

  # -------------------------------------------------------------------------
  sub product_name
  {
    my $self = shift;

    if (@_) 
    {
      $self->[$PRODUCT] = shift;
    }

    return($self->[$PRODUCT]);
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
  sub rs_sched
  {
    my $self = shift;

    if (@_) 
    {
      $self->[$SCHED_SUBR] = shift;
    }

    return($self->[$SCHED_SUBR]);
  }


  # -------------------------------------------------------------------------
  sub rs_environment
  {
    my $self = shift;

    if (@_) 
    {
      $self->[$ENVIRONMENT_SUBR] = shift;
    }

    return($self->[$ENVIRONMENT_SUBR]);
  }

  # -------------------------------------------------------------------------
  sub rs_variables
  {
    my $self = shift;

    if (@_) 
    {
      $self->[$VARIABLES_SUBR] = shift;
    }

    return($self->[$VARIABLES_SUBR]);
  }

  # -------------------------------------------------------------------------
  sub rs_variables_impl
  {
    my $self = shift;

    if (@_) 
    {
      $self->[$VARIABLES_IMPL_SUBR] = shift;
    }

    return($self->[$VARIABLES_IMPL_SUBR]);
  }


  # -------------------------------------------------------------------------
  sub rs_monitor
  {
    my $self = shift;

    if (@_) 
    {
      $self->[$SUBR_COMPILED] = shift;
    }

    return($self->[$SUBR_COMPILED]);
  }

  # -------------------------------------------------------------------------
  sub rs_emonitor
  {
    my $self = shift;

    if (@_) 
    {
      $self->[$ESUBR_COMPILED] = shift;
    }

    return($self->[$ESUBR_COMPILED]);
  }

  # -------------------------------------------------------------------------
  sub rs_precalc
  {
    my $self = shift;

    if (@_) 
    {
      $self->[$PRECALC_SUBR] = shift;
    }

    return($self->[$PRECALC_SUBR]);
  }

  # -------------------------------------------------------------------------
  sub rs_threshold
  {
    $DEBUG && TraceFuncs::trace(my $f);
    my $self = shift;

    if (@_) 
    {
      $self->[$THRESHOLD_SUBR] = shift;
    }

    $DEBUG && TraceFuncs::debug($self->[$THRESHOLD_SUBR]);
    return($self->[$THRESHOLD_SUBR]);
  }


  # -------------------------------------------------------------------------
  sub rs_assign_row
  {
    my $self = shift;

    if (@_) 
    {
      $self->[$ASSIGN_ROW_SUBR] = shift;
    }

    return($self->[$ASSIGN_ROW_SUBR]);
  }


  # -------------------------------------------------------------------------
  sub columns
  {
    my $self = shift;

    if (@_) 
    {
      $self->[$COLS] = shift;
    }
    elsif ( ! defined($self->[$COLS]) )
    {
      if ( defined($FT::MONITOR::COLS) )
      {
        $self->[$COLS] = $FT::MONITOR::COLS;
      }
    }
    return($self->[$COLS]);
  }


  # -------------------------------------------------------------------------
  sub severity
  {
    my $self = shift;
    return($self->[$HIGHEST_SEV]);
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
  sub interval
  {
    $DEBUG && TraceFuncs::trace(my $f);
    my $self = shift;

    $DEBUG && TraceFuncs::debug("INTERVAL = " . $self->[$INTERVAL]);
    return $self->[$INTERVAL];
  }

  # -------------------------------------------------------------------------
  sub last_run
  {
    $DEBUG && TraceFuncs::trace(my $f);
    my $self = shift;

    $DEBUG && TraceFuncs::debug("LAST_RUN = " . $self->[$LAST_RUN]);
    return $self->[$LAST_RUN];
  }

  # -------------------------------------------------------------------------
  sub run_time
  {
    $DEBUG && TraceFuncs::trace(my $f);
    my $self = shift;

    $DEBUG && TraceFuncs::debug("RUN_TIME = " . $self->[$RUN_TIME]);
    return $self->[$RUN_TIME];
  }


  # ------------------------------------------------------------------------
  # run
  #
  # pre-condition:
  #   - monitor is scheduled to run
  # post-condition:
  #   - monitor has 
  #          - retrieved the associated table data,
  #          - performed calculations 
  #          - generated events for any thresholds that have been breached.
  #          - perform actions and generated html status pages
  sub run
  {
    $DEBUG && TraceFuncs::trace(my $f);
    my $self = shift;

    $DEBUG && TraceFuncs::debug("Running monitor " . ($self->name()) );

    return($FTMON::Job::MAINTENANCE, "") if ( $self->[$STATE] == 0 );

    my $start_time = time();
    $FT::MONITOR::INTERVAL = 0;
    $FT::MONITOR::INTERVAL = $self->[$INTERVAL] = 
                             ( $start_time - $self->last_run() )
                             if ( $self->last_run() );
    $FT::MONITOR::RUN_TIME = $self->run_time();
    $FT::MONITOR::LAST_RUN = $self->last_run();

    my $event_id;
    my @monitor_events = ();

    die "No event manager defined. You must define 'FT::EVENT_MGR'."
         if ( ! defined($FT::EVENT_MGR) );

    $DEBUG && TraceFuncs::debug(
             "EventManager name is " . $FT::EVENT_MGR->name());

    $FT::VENDOR        = $self->vendor_name();
    $FT::PRODUCT       = $self->product_name();
    $FT::MONITOR::NAME = $self->monitor_name();

    $FT::PACKAGE = 
       $FT::VENDOR . "::" . $FT::PRODUCT . "::" . $FT::MONITOR::NAME;

    $FT::MONITOR::DESC = $self->description();
    $FT::MONITOR::VER  = $self->version();


    my $calc_mgr = $FTMON::CalculationManager::SINGLETON;
    $calc_mgr->active_monitor($self->name());

    # Endeavour to force standard on event naming by providing
    # A standard event identifier.
    $FT::BASE_I = $FT::VENDOR . "/" . $FT::PRODUCT . "/" . $FT::MONITOR::NAME;


    %FT::MONITOR::INFO = ();
    $FT::CONFIG_STR = "";
    $self->[$HIGHEST_SEV] = $FT::SEV[0];


    #
    # Configuration Files are referenced relative to the directory
    # containing the monitor threshold file.
    #
    my $monitor_dir = 
          $FTMON::Environment::SINGLETON->cfg_dir() . "/" .
          $self->vendor_name() . "/" .
          $self->product_name();

    chdir($monitor_dir)
       || die $self->name() .
              ": could not change to monitor directory ($monitor_dir";


    # By defaults events are closed by the EventManager for all monitors 
    # unless this variable has been specifically unset to override this
    # in the varialbe initialization functions.
    $FT::CLOSE_EVENTS = 1;

    &{$self->rs_variables_impl()}();
    &{$self->rs_variables()}();


    @FT::VALUES = ();
    $FT::TRADING_MSG{$FT::VENDOR . "::" . $FT::PRODUCT} = "N/A";

    my $threshold;
    my $thresholds = [];
    my $thresholds_raw = [];
    my $i;
    my $row;

    my $config_file = $self->config_file();

    #
    # If the monitor returns an error then go thru the configuration 
    # thresholds to find a matching error message to display. Error thresholds
    # are identified by undefined values in the resource column.
    #
    if ( ! &{$self->rs_monitor()}() )
    {
      $calc_mgr->active_resource("");

      my $threshold_index = 0;

      if ( @FT::VALUES )
      {
        $row = $FT::VALUES[0];
        &{$self->rs_assign_row()}(@{$row});
      }

      &{$self->rs_precalc()}();

      $thresholds_raw = &{$self->rs_threshold()}();
      $thresholds = $config_file->thresholds();
      foreach $threshold (@$thresholds)
      {
        $threshold->initialise($thresholds_raw->[$threshold_index ++]);
        next if ( defined($threshold->resource()) );
        $event_id = $threshold->eventid();
        if ( $threshold->calculation() )
        {
          my $event = $FT::EVENT_MGR->open_event( $event_id, $self, $threshold );
          if ( $event )
          {
            push(@monitor_events, $event);

            $self->[$HIGHEST_SEV] = $event->severity()
            if ( FT::ordered_sev($self->[$HIGHEST_SEV]) <  
                 FT::ordered_sev($event->severity()) );
          }
        }
      }
    }

    # REVISIT: Split out following into separate subroutines.
    else
    {
      my %exact_match = ();
      my $i = 0;
      %FT::VALUES= ();
  
      # Create hash based on resource id for rank type calculations.
      foreach $row (@FT::VALUES)
      {
        &{$self->rs_assign_row()}(@{$row});
        $FT::VALUES{$FT::RESOURCE} = $row;
      }
  
      foreach $row (@FT::VALUES)
      {
        $DEBUG && TraceFuncs::debug("\n\n--> ROW " . $i . " - " . $row->[0]);
  
        #
        # Ensure variables are initialised to null.
        # NB Assumes people have implemented monitor correctly.
        #
        &{$self->rs_variables_impl()}();
        &{$self->rs_variables()}();
  
        #
        # Assign row of data columns to variables.
        #
        $FT::ROW = $row;
        &{$self->rs_assign_row()}(@{$row});

        #
        # Check for resources that should be skipped from threshold checking
        # and at the same time initialise the thresholds.
        #
        my $thresholds = $config_file->thresholds();
        my $skip_resource = $self->skip_thresholds($thresholds, $FT::RESOURCE);
        if ( ! $skip_resource )
        {
          # REVISIT: not sure if this is working.
          $calc_mgr->active_resource($FT::RESOURCE);
    
          $FT::MONITOR::BASELINED = 0;
          &{$self->rs_precalc()}();
    
          # Set time monitor was baselined if applicable.
          $self->baselined($FT::MONITOR::BASELINED) 
                 if ( $FT::MONITOR::BASELINED );
  
          # Check resource values against thresholds.
          $self->check_thresholds(
                 $thresholds, \@monitor_events, \%exact_match, $FT::RESOURCE);
        }
      }
  
  
      # Run any Post processing that is configured.
      &{$self->rs_emonitor()}() if ( defined $self->rs_emonitor() );
 
 
      #
      # Check the heartbeat and discover events
      #
      my $thresh_str = "";
      my @left_overs = keys %FT::VALUES;
      my @exact_match = keys %exact_match;
      
      $DEBUG && TraceFuncs::debug("Check Heartbeats/Discovery");
      foreach $threshold (@$thresholds)
      {
  
        next if ( ! defined($threshold->calculation() ) );
        my $result = $threshold->calculation();
  
        next if ( ! defined($threshold->resource() ));
        my $threshold_resource = $threshold->resource();
  
        next if ( ref($threshold_resource) ne "ARRAY" );
  
        $event_id = $threshold->eventid();
        $DEBUG && TraceFuncs::debug("event_id = " . $event_id);
  
        $FT::HB::RESOURCE = "";
        if ( ref ($threshold_resource->[0]) eq "ARRAY" )
        {
          $threshold_resource = $threshold_resource->[0]->[0];
          $DEBUG && TraceFuncs::debug("hearbeat - " . $threshold_resource);
          if ( $threshold_resource =~ /^\/(.*)\/$/ )
          {
            next if ( grep(/$1/, @left_overs) );
          }
          else
          {
            next if ( grep { $_ eq $threshold_resource} @left_overs );
          }
  
          $FT::HB::RESOURCE = $threshold_resource;
          $calc_mgr->active_resource($FT::HB::RESOURCE);
          &{$self->rs_threshold()}();
          $event_id = $threshold->eventid();
          $DEBUG && TraceFuncs::debug("event_id = " . $event_id);
  
          $DEBUG && TraceFuncs::debug(
                 "match $threshold_resource");  
          if ( $result )
          {
            my $event = $FT::EVENT_MGR->open_event( 
                             $event_id, $self, $threshold );
            if ( $event )
            {
              push(@monitor_events, $event);
                 $self->[$HIGHEST_SEV] = $event->severity()
                   if ( FT::ordered_sev($self->[$HIGHEST_SEV]) < 
                        FT::ordered_sev($event->severity()) );
            }
          }
        }
        else
        {
          $threshold_resource = $threshold_resource->[0];
          my @discovered;
          $DEBUG && TraceFuncs::debug("discover - " . $threshold_resource);
          if ( $threshold_resource =~ /^\/(.*)\/$/ )
          {
            @discovered = grep(/$1/, @left_overs);
          }
          else
          {
            @discovered = grep { $_ eq $threshold_resource} @left_overs;
          }
  
          my @undiscovered = ();
          my $severity = "UNKNOWN";
          foreach $discovered (@discovered)
          {
            # REVISIT
            #
            $severity = $threshold->severity()->str()
            if (defined $threshold->severity() );
  
            $exact_match = 
              $threshold->eventid_str() . "/" . 
              $discovered . "/" .
              $severity;
  
            my $resource_str = $discovered;
  
            $DEBUG && TraceFuncs::debug("exact_match - " . $exact_match);
            if ( ! grep { $_ eq $exact_match } @exact_match )
            {
              $thresh_str = $threshold->str();
                    $DEBUG && TraceFuncs::debug("DISCOVERED: $thresh_str");
  
              #
              # Need to escape back slashes otherwise we will get errors when
              # we load the threshold values. REVISIT: very cludgy
              # 
              $resource_str =~ s/\\/\\\\/g;
              $DEBUG && TraceFuncs::debug("DISCOVERED: $resource_str");
  
              $thresh_str =~ s/^\s*\[\s*\[.*?\]\s*\,/  \[\'$resource_str\'\,/;
              $DEBUG && TraceFuncs::debug("DISCOVERED:\n$thresh_str\n\n");
              $FT::CONFIG_STR = $FT::CONFIG_STR . $thresh_str;
              $DEBUG && 
                TraceFuncs::debug("FT::CONFIG_STR\n$FT::CONFIG_STR\n\n");
            }
          }
        }
      }
    }


    $DEBUG && TraceFuncs::debug("close_events");
    $FT::EVENT_MGR->close_events($self);

    #
    # Update the info. display
    #
    my $package;
    my $entry;
    my $id;
    my $resource;
    my $info;
    while ( ( $resource, $entry ) = 
         each %{$FT::MONITOR::PERSIST_INFO{$FT::PACKAGE}} )
    {
      while ( ($id, $info) = each %{$entry} )
      {
        push(@{$FT::MONITOR::INFO{$resource}}, $info);
      }
    }


    while ( ( $resource, $info ) = 
            each %{$FT::MONITOR::POST_INFO{$FT::PACKAGE}} )
    {
      $DEBUG && TraceFuncs::debug(
         "POST_INFO $FT::PACKAGE resource = $resource");
      $FT::MONITOR::INFO{$resource} = $info;
    }
    %{$FT::MONITOR::POST_INFO{$FT::PACKAGE}} = ();


    $self->dump_info($FT::PACKAGE, \%FT::MONITOR::INFO, \@monitor_events);


    if ( $FT::CONFIG_STR )
    {
      $self->[$CONFIG_FILE]->merge_config_str($FT::CONFIG_STR);
    }

    $self->[$LAST_RUN] = time();
    $self->[$RUN_TIME] = $self->[$LAST_RUN] - $start_time;

    return($FTMON::Job::OK, "");
  }


# ----------------------------------------------------------

  BEGIN
  {
    my(@col_names) = 
           ("Severity", "Time", "Description", "Count" );

    # post-condition:
    #    - open event sorted and dumped to html file
    sub dump_events
    {
      $DEBUG && TraceFuncs::trace(my $f);

      my $self = shift;
      local(*fh) = shift;
      my $events = shift;
      


      FTMON::Helper::http_table_start(*fh, "", \@col_names);

      my $event = undef;
      my @event_details = ();
      my $severity;
      # foreach $event ( @{$self->[$_SENT_EVENT_LIST]} )

      my @sorted_keys = 
        sort 
        { 
          my $sa = $a->severity();
          my $sb = $b->severity();

          defined $sa &&
          defined $sb &&
          FT::ordered_sev($sb) <=> FT::ordered_sev($sa);
        } @{$events};

      foreach $event ( @sorted_keys )
      {

        next if ( ! defined $event->severity()  ||
                $event->severity() eq "NOEVENT" ||
                $event->severity() eq "" );

        $DEBUG && TraceFuncs::debug($event->event_id());
        my $severity = $event->severity();
        my @severity = ();
        my $fg_color = "white";
        my $bg_color = "black";
        $fg_color = $FT::SEVERITY_FG_COLOR{$severity}
                 if ( exists($FT::SEVERITY_FG_COLOR{$severity}) );
        $bg_color = $FT::SEVERITY_BG_COLOR{$severity}
                 if ( exists($FT::SEVERITY_BG_COLOR{$severity}) );

        if ( $fg_color && $bg_color )
        {
          push(@severity, $bg_color);
          push(@severity, $fg_color);
        }
        push(@severity, $severity);

        my $event_id = $event->event_id();
        my ($vendor, $product, $monitor)= split("/", $event_id);
        # my $html_path = $FTMON::Environment::SINGLETON->html_dir() . "/"  .
        my $html_path = $vendor . "/" .
            $product . "/" .
            $monitor . ".html";
        #if ( -f $html_path  )
        #{
          $event_id = "<a href=\"$html_path\">$event_id</a>";
        #}
        push(@event_details,
          [ @severity,
            FT::convert_date(
              '$dd $Mth $yyyy $hh:$mm:$ss', 
              $event->timestamp()),
            $event->message(),
            $event->repeat_count(), 
          ] );
      }

      $fmt_str = 
          '<TR><TD WIDTH="15%%" BGCOLOR="%s"><LEFT>' .
          '<FONT COLOR="%s">%s</COLOR></LEFT></TD>' .
          '<TD WIDTH="20%%"><FONT SIZE=-1><LEFT>%s</LEFT></FONT></TD>' .
          '<TD WIDTH="55%%"><FONT SIZE=-1><LEFT>%s</LEFT></FONT></TD>' .
          '<TD WIDTH="10%%"><FONT SIZE=-1><LEFT>%s</LEFT></FONT></TD></TR>';

      FTMON::Helper::print_table(*fh, \@event_details, '-1', $fmt_str);
      FTMON::Helper::http_table_end(*fh);
    }
  };

# ---------------------------------------------------------------------
# Returns TRUE (1) if current resource
# matches a skip threshold.
# ---------------------------------------------------------------------
sub skip_thresholds
{
  my $self = shift;
  my $thresholds = shift;
  my $resource = shift;

  my $skip_resource = 0;
  my $threshold;
  my $threshold_resource;
  my $threshold_index = 0;
  my $thresholds_raw = &{$self->rs_threshold()}();
  foreach $threshold (@$thresholds)
  {
    $threshold->ref($thresholds_raw->[$threshold_index ++]);

    next if ( ! defined($threshold->resource() ));
    my $threshold_resource = $threshold->resource();
  
    # skip resources are identified by anundefined threshold and
    # eventid fields.
    if ( ! defined $threshold->calculation() &&
         ! defined $threshold->eventid() )
    {
      if ( $threshold_resource =~ /^\/(.*)\/$/ )
      {
        if ( $resource =~ /$1/ )
        {
          $DEBUG && TraceFuncs::debug("skip $resource");  
          delete $FT::VALUES{$resource};
          return(1);
        }
      }
      elsif ( ref($threshold_resource) )
      {
        my $type = ref($threshold_resource);
        $DEBUG && TraceFuncs::debug("$threshold_resource is $type");
      }
      else
      {
        if ( $resource eq ${threshold_resource} )
        {
          $DEBUG && TraceFuncs::debug("skip $resource");  
          delete $FT::VALUES{$resource};
          return(1);
        }
      }
      next;
    }
  }

  return(0);
}

# ------------------------------------------------------------------------
# Checks monitor values against thresholds, returns hash of resources
# that match exact match thresholds.
# ------------------------------------------------------------------------
sub check_thresholds
{
  my $self = shift;
  my $thresholds = shift;
  my $monitor_events = shift;
  my $exact_match = shift;
  my $resource = shift;

  my $threshold;
  my $threshold_index = 0;
  my $thresholds_raw = &{$self->rs_threshold()}();
  foreach $threshold (@$thresholds)
  {
    $threshold->initialise($thresholds_raw->[$threshold_index ++]);

    next if ( ! defined $threshold->resource() );
    next if ( ! defined $threshold->calculation() && 
              ! defined $threshold->eventid() );

    my $threshold_resource = $threshold->resource();
  
  
    $event_id = $threshold->eventid();
    $DEBUG && TraceFuncs::debug(
           "\nresource= " . $resource .
           "\nresource=     " . $threshold_resource .
           "\nevent_id=     " . $event_id );
  
    $DEBUG && TraceFuncs::debug( 
              "Compare " .
              $resource .  
              " with $threshold_resource" );
  
    if ( $threshold_resource =~ /^\/(.*)\/$/ )
    {
      $DEBUG && TraceFuncs::debug("$threshold_resource is //");
      next if ( $resource !~ /$1/ );
    }
    elsif ( ref($threshold_resource) )
    {
      my $type = ref($threshold_resource);
      $DEBUG && TraceFuncs::debug("$threshold_resource is $type");
      next;
    }
    else
    {
      $DEBUG && TraceFuncs::debug("$threshold_resource is string");
      $DEBUG && TraceFuncs::debug(
              "compare " . $resource . " with " . $threshold_resource );
      next if ( $resource ne $threshold_resource );
      # REVISIT: why did previously use $event_id
      # $event_id . "/" . 
      $exact_match_key = 
               $threshold->eventid_str() . "/" . 
               $resource . "/" .
               $threshold->severity()->str();
  
      $exact_match{$exact_match_key} = $FT::VALUES{$resource};
      $DEBUG && TraceFuncs::debug("exact_match $exact_match_key");
    }
    $DEBUG && TraceFuncs::debug(
                 "match $resource - $threshold_resource" );
  
    # REVISIT
    #delete $FT::VALUES{$resource};
  
    my $thresh_str;
    my $result = 0;
    my $result = $threshold->calculation();
    # REVISIT
    die "No calculation defined: " . 
        $FT::RESOURCE . " " . 
        $threshold->calculation_str() if ( ! defined $result );
  
    $DEBUG && TraceFuncs::debug("threshold_result = $result");
  
    if ( $result )
    {
      $DEBUG && TraceFuncs::debug("result = $result\n");
      my $event = $FT::EVENT_MGR->open_event( $event_id, $self, $threshold );
      if ( $event )
      {
        push(@$monitor_events, $event);
        $self->[$HIGHEST_SEV] = $event->severity()
        if ( defined $event->severity() &&
                $event->severity() &&
                FT::ordered_sev($self->[$HIGHEST_SEV]) < 
                FT::ordered_sev($event->severity()) );
              $FT::SEVERITY = $self->[$HIGHEST_SEV];
       }
    }
  }
}

  # ------------------------------------------------------------------------
  # post-condition:
  #  - open event sorted and dumped to html file
  sub dump_info
  {
    $DEBUG && TraceFuncs::trace(my $f);
    my $self = shift;
    my $package = shift;
    my $info = shift;
    my $monitor_events = shift;

    my $config_file = $self->config_file();

    my(@col_names) = ("Resource", "Value");
    ($FT::VENDOR, $FT::PRODUCT, $FT::MONITOR::NAME) = split("::", $package);


    # REVISIT
    my $html_file = $FT::MONITOR::NAME . ".html";
    $html_file =~ s/::/_/g;
    $html_file =~ s/\//__/g;

    my $html_dir = 
        $FTMON::Environment::SINGLETON->html_dir() . "/"  .  $FT::VENDOR;
    if ( ! -d $html_dir )
    {
      mkdir($html_dir, 0755) || die "Can not make $html_dir";
    }

    $html_dir = $html_dir . "/"  .  $FT::PRODUCT;
    if ( ! -d $html_dir )
    {
      mkdir($html_dir, 0755) || die "Can not make $html_dir";
    }

    # my $product = $self->[$PRODUCTS]->{$FT::VENDOR . "::" . $FT::PRODUCT}

    my $product = $FT::VENDOR . "::" . $FT::PRODUCT;
    my $product_file = $html_file;
    $product_file =~ s/${FT::MONITOR::NAME}\.html$/index\.html/;


    my $html_path = $html_dir . "/"  .  $html_file;
    

    open(HTML,  "> $html_path") || 
         die "Could not open $html_path - $!";


    my $info_file = $html_file;
    $info_file =~ s/\.html$/_info\.html/;

    my $status_file = $html_file;
    $status_file =~ s/\.html$/_status\.html/;

    #my $events_file = $html_file;
    #$events_file =~ s/\.html$/_events\.html/;

    my $graph_file = $html_file;
    $graph_file =~ s/\.html$/_graph\.html/;

    my $cfg_file = $html_file;
    $cfg_file =~ s/\.html$/_cfg\.html/;

    my $param_file = $html_file;
    $param_file =~ s/\.html$/_param\.html/;

    my $http_header =
       "<a href=\"$html_file\"><b>[events]</b></font></a>&nbsp;" .
       "<a href=\"$info_file\">[info]</font></a>&nbsp;" .
       "<a href=\"$status_file\">[status]</a>&nbsp;" .
       "<a href=\"$graph_file\">[graphs]</a>&nbsp;" .
       "<a href=\"$param_file\">[parameters]</a>&nbsp;" .
       "<a href=\"$cfg_file\">[thresholds]</a>&nbsp;" .
       "<a href=\"$product_file\">[$product]</a>&nbsp;";

       # "<table width=\"89\">" .
       #"<td width=\"87\" bgcolor=\"$bg_color\">" .


    #
    # Events
    #

    #$html_path = $html_dir . "/"  .  $events_file;
    

    open(HTML,  "> $html_path") || 
         die "Could not open $html_path - $!";

    FTMON::Helper::http_page_begin(*HTML, 
       $package, 
       60,
       $http_header,
       "../../");

    print HTML "<P>The following table contains event conditions detected by the monitor.</P><br>";
    $self->dump_events(*HTML, $monitor_events);
    FTMON::Helper::http_page_end(*HTML);

    print HTML "<br>\n";



    $http_header =~ s/<b>//g;
    $http_header =~ s/<\/b>//g;
    $http_header =~ s/\[info\]/<b>\[info\]<\/b>/;

    # ------------------
    $html_path = $html_dir . "/"  .  $info_file;

    open(INFO,  "> $html_path") || 
         die "Could not open $html_path - $!";

    my $severity = $self->[$HIGHEST_SEV];
    my $fg_color = "white";
    my $bg_color = "black";
    $fg_color = $FT::SEVERITY_FG_COLOR{$severity}
                   if ( exists($FT::SEVERITY_FG_COLOR{$severity}) );
    $bg_color = $FT::SEVERITY_BG_COLOR{$severity}
                   if ( exists($FT::SEVERITY_BG_COLOR{$severity}) );


     my $baseline_str = 
       ( ! $self->baselined() ) 
            ? ""
            : "<b>LastBaseline:</b> " . 
               FT::convert_date(
           '$dd $Month $yyyy $hh:$mm:$ss',
            $self->baselined()) . 
            " (" . 
            FT::days2str( ( time() - $self->baselined()) / 24 / 3600 ) . 
            " ago) <br>";

    my $last_run = 
          FT::convert_date(
      '$dd $Month $yyyy $hh:$mm:$ss',
       $FT::MONITOR::LAST_RUN) .
          " (" . 
          FT::days2str( ( time() - $FT::MONITOR::LAST_RUN) / 24 / 3600 ) . 
          " ago)";
    $last_run =  "never" if ( ! $FT::MONITOR::LAST_RUN );

    my $compile_time = 
          FT::convert_date(
      '$dd $Month $yyyy $hh:$mm:$ss',
       $config_file->compile_time()) .
          " (" . 
         FT::days2str( ( time() - $config_file->compile_time()) / 24 / 3600 ) . 
          " ago)";

    FTMON::Helper::http_page_begin(*INFO, 
       $package, 
       60,
       $http_header .
       "<table>" .
       "<tr>" .
       "<td bgcolor=\"$bg_color\">" .
       "<font color=\"$fg_color\">$severity</font></a>&nbsp;" .
       "</td>" .
       "<td justify=\"center\"><br><H3>$package</H3></td></tr> </table>" .
       "<P><b>Description:</b> " . $self->description() . "<br>" .
       "<b>Version:</b> " . $self->version() . "</P>" .
       "<P>" . $config_file->description() . "</P>",
       "../../");

       # REVISIT: These go to the Monitors - monitoring page.
       #"<b>CompileTime:</b> " . $compile_time . "<br>" .
       #"<b>MD5:</b> " . $config_file->md5() . "<br>" .
       #"<b>LastRun:</b> " . 
       #$last_run .
       #"<br>" .
       #$baseline_str .
       #"<b>RunTime:</b> " . $FT::MONITOR::RUN_TIME . " secs</P>",
       #"../../");

    FTMON::Helper::http_table_start(
       *INFO,  
       "$FT::PRODUCT attributes monitored by this monitor.",
       [ "Name", "Description" ]);

    my @variable_names = $config_file->variable_names();
    my $variable_name;
    my @variable_details;
    my @calculation_details;
    my $variable_data;
    my $comment;
    foreach $variable_name ( @variable_names )
    {
       if ( $variable_name =~ /_V$/ )
       {
         $variable_data = $config_file->variable_data($variable_name);
   if ( ! $variable_data->[1] )
   {
           $comment = $variable_data->[0];
           push(@calculation_details, 
        [$variable_name, $comment, $variable_data->[2]]);
   }
   else
   {
           $comment = $variable_data->[1];
           push(@variable_details, 
        [$variable_name, $comment, $variable_data->[2]]);
   }
       }
    }
    # push(@variable_details, @calculation_details); 
    FTMON::Helper::print_table(*INFO, \@variable_details);
    FTMON::Helper::http_table_end(*INFO);
    

    # ------------------
    $html_path = $html_dir . "/"  .  $param_file;
    

    open(PARAM,  "> $html_path") || 
         die "Could not open $html_path - $!";

    $http_header =~ s/<b>//g;
    $http_header =~ s/<\/b>//g;
    $http_header =~ s/\[parameters\]/<b>\[parameters\]<\/b>/;

    FTMON::Helper::http_page_begin(*PARAM, 
       $package, 
       60,
       $http_header,
       "../../");

    print PARAM "<P>The following table contains parameters that can be adjusted for this monitor.</P><br>";

    FTMON::Helper::http_table_start(
       *PARAM,  
       "Parameters for adjusting $FT::PRODUCT monitor settings",
       [ "Name", "Description", "Default" ]);

    @variable_details = ();
    foreach $variable_name ( @variable_names )
    {
       if ( $variable_name =~ /_P$/ )
       {
         $variable_data = $config_file->variable_data($variable_name);
         push(@variable_details, 
     [$variable_name, $variable_data->[1], $variable_data->[0]]);
       }
    }
    FTMON::Helper::print_table(*PARAM, \@variable_details);
    FTMON::Helper::http_table_end(*PARAM);

    my $resource = undef;
    my $value = undef;
    my @resource_details = ();
    my @sorted_keys = sort { $b cmp $a } keys %{$info};
    my $last_monitor = "";
    my $header = "";
    my $old_header = "";


    #
    # Status
    #


    $html_path = $html_dir . "/"  .  $status_file;
    

    open(INFO,  "> $html_path") || 
         die "Could not open $html_path - $!";

    $http_header =~ s/<b>//g;
    $http_header =~ s/<\/b>//g;
    $http_header =~ s/\[status\]/<b>\[status\]<\/b>/;


    FTMON::Helper::http_page_begin(*INFO, 
       $package, 
       60,
       $http_header,
       "../../");

    print INFO "<P>The following table contains the current values of monitors.</P><br>";


    FTMON::Helper::http_table_start(
       *INFO,  
       "Resource Status" );
    foreach $resource ( @sorted_keys )
    {
      $value = $info->{$resource};
      my $entry = $value;

      if ( ref($value) eq "ARRAY" )
      {
        $entry = "";

  my @max_width = ();
  my $j;
  foreach $row ( @{$value} )
  {
    $j = 0;
    foreach $col ( @{$row} )
    {
      my $length = length(" " . $col->[0]);
            if ( ! defined $max_width[$j] || 
                 $max_width[$j] < $length )
            {
              $max_width[$j] = $length;
            }

      my $length = length(" " . $col->[1]);
            if ( ! defined $max_width[$j] || 
                 $max_width[$j] < $length )
            {
              $max_width[$j] = $length;
            }
      $j++;
    }
  }

  my $total_width = 0;
  foreach ( @max_width )
  {
    $total_width = $total_width + $_;
  }

  foreach ( @max_width )
  {
    $_ = 100 * $_ / $total_width;
  }


  foreach $row ( @{$value} )
  {
    $entry .= "  <TR>\n";
    $header = "  <TR>\n";
    my $j = 0;
    foreach $col ( @{$row} )
    {
      my $width = $max_width[$j] + 1;
      $header .= "  <TH WIDTH=\"" . 
                 $width . "\%\"><FONT SIZE=-1><CENTER><b>" . 
                 $col->[0] . "</CENTER></b></FONT></TH>";
      $entry  .= "  <TD WIDTH=\"$width\%\"><FONT SIZE=-1>&nbsp;" . 
                 $col->[1] . "</FONT></TD>";
      $j++;
    }

    if ( $header ne $old_header )
    {
             $entry = $header . " </TR>" . $entry . "  </TR>\n";
          }
          else
          {
             $entry = $entry . "  </TR>\n";
          }
    $old_header = $header;
         }
         $entry = "<TABLE BORDER=1 CELLPADDING=\"0\" CELLSPACING=\"3\">" .
                  "<CAPTION><b>" . $resource . "</b></CAPTION>" . 
                  $entry . "</TABLE>\n"; 

       }

       @resource_details = ();
       #push(@resource_details, [ $resource, $entry ] );
       push(@resource_details, [ $entry ] );
       FTMON::Helper::print_table(*INFO, \@resource_details);
    }
    FTMON::Helper::http_table_end(*INFO);

    FTMON::Helper::http_page_end(*INFO);
    close(INFO);

  
    @col_names = ( "resource" );

    print HTML "<br>\n";

    #
    # Graphs.
    #


    $html_path = $html_dir . "/"  .  $graph_file;
    

    open(GRAPH,  "> $html_path") || 
         die "Could not open $html_path - $!";

    $http_header =~ s/<b>//g;
    $http_header =~ s/<\/b>//g;
    $http_header =~ s/\[graphs\]/<b>\[graphs\]<\/b>/;

    FTMON::Helper::http_page_begin(*GRAPH, 
       $package, 
       60,
       $http_header,
       "../../");

    print GRAPH "<A HREF=\"http://ee-staff.ethz.ch/~oetiker/webtools/rrdtool/\"><img border=\"0\" "  .
    "src=\"../../rrdtool.gif\" alt=\"\" width=\"120\" height=\"34\"></A>";
    print GRAPH "<P>If configured, links to RRD generated graphs will slowly appear below.</P>"; 

    $DEBUG && TraceFuncs::debug("Dumping Graphs for $FT::PACKAGE");



    #
    # If there has been no rrd update for some time - then cleanup the rrd file.
    #
    my @keep_rrds = ();
    my @delete_rrds = ();
    my $rrd;

    foreach $rrd ( @{$FT::RRD{$FT::PACKAGE}} )
    {
      my $graphs = $rrd->list_graphs();
      foreach $entry ( @$graphs )
      {
        ( $resource, $html_file ) = @$entry;
        $DEBUG && TraceFuncs::debug("file = $html_file");

        @resource_details = ();
        print GRAPH "<p><width=700><a href=\"$html_file\">$resource</a></width></p>\n";
      }

      if ( 
           ( $self->interval() &&
       $rrd->interval() > ( $rrd->step() * 2 ) * $self->interval() 
           ) ||
     ( 
             $rrd->last_run() && $self->interval() && 
             ( time() - $rrd->last_run() )  
          > ( $rrd->step() * 2 ) * $self->interval()
     )
         )
      {
        push(@delete_rrds, $rrd);
        next;
      }

      push(@keep_rrds, $rrd);
        
    }
    @{$FT::RRD{$FT::PACKAGE}} = @keep_rrds;


    #
    # Cleanup memory for deleted rrds.
    #
    foreach $rrd (@delete_rrds )
    {
      $rrd->deleted(1);
    }
    @delete_rrds = ();


    FTMON::Helper::http_page_end(*GRAPH);
    close(GRAPH);

    print HTML "<br>\n";

    #
    # Configuration.
    #

    $html_path = $html_dir . "/"  .  $cfg_file;
    

    open(CONFIG,  "> $html_path") || 
         die "Could not open $html_path - $!";

    $http_header =~ s/<b>//g;
    $http_header =~ s/<\/b>//g;
    $http_header =~ s/\[thresholds\]/<b>\[thresholds\]<\/b>/;

    FTMON::Helper::http_page_begin(*CONFIG, 
       $package, 
       60,
       $http_header,
       "../../");

    #FTMON::Helper::perl2html($self->[$CONFIG_FILE]->name(), *HTML);
    print CONFIG "<P>The following table contains the thresholds currently set for this monitor. You can adjust these thresholds by editing the file referenced in the caption below.</P><br>";

    @col_names = ( "Resource", "Calculation", "Severity", "EventID",
                   "Message", "Action" );

    my $caption = 
         $self->[$CONFIG_FILE]->name() .  " (" .
         FT::convert_date(
      '$dd $Month $yyyy $hh:$mm:$ss',
      $self->[$CONFIG_FILE]->compile_time()) . ") MD5=" .
         $self->[$CONFIG_FILE]->md5();

    FTMON::Helper::http_table_start(
       *CONFIG,  
       $caption,
       \@col_names);

    my $thresholds = $self->[$CONFIG_FILE]->thresholds();
    
    foreach $threshold ( @$thresholds )
    {
      my $html_str = "\n      <TABLE>\n";
      my $resource = $threshold->resource_str() || "undef";

      my $calculation = $threshold->calculation_str() || "undef";

      my $severity = ( defined $threshold->severity() )
                       ? $threshold->severity()->dump_html(1) : "-";

      my $eventid = $threshold->eventid_str() || "-";

      my $message = ( defined $threshold->message() )
                       ? $threshold->message_str() : "-";

      my $action  = ( defined $threshold->action() )
                     ? $threshold->action_str() : "-";

      my @attrib = 
                ( $calculation,
                  $severity,
                  $eventid,
                  $message,
                  $action
    );
      
      @resource_details = ();
      push(@resource_details, [ $resource, @attrib ] );
      FTMON::Helper::print_table(*CONFIG, \@resource_details, "-1");
    }
    FTMON::Helper::http_table_end(*CONFIG);
    FTMON::Helper::http_page_end(*CONFIG);
    close(CONFIG);


    FTMON::Helper::http_page_end(*HTML);

    close(HTML);
  }

1;

__END__;

######################## User Documentation ##########################

## To format the following documentation into a more readable format,
## use one of these programs: perldoc; pod2man; pod2html; pod2text.
## For example, to nicely format this documentation for printing, you
## may use pod2man and groff to convert to postscript:

=head1 NAME

FTMON::Monitor - 

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
