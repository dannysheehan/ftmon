package FTMON::ConfigFile;
#############################################################################
#                    FTMON (Fast Track Systems Monitor)
#
#   Script: @(#) $RCSfile: ConfigFile.pm,v $ 
# 
#   DESCRIPTION: 
#
#   @(#) Responsible for managing a Configuration File.
#
#   $Source: /cvsroot/ftmon/base2/lib/FTMON/ConfigFile.pm,v $
#
#   $Date: 2003/01/10 13:10:51 $
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
#      PO Box 238
#      Eastwood NSW 2122
#      AUSTRALIA
#      dsheehan@ftmon.org
#      http://ftmon.org
#
#############################################################################
require 5.002;

# use strict;
# use warnings;
# use Carp;

use TraceFuncs;
use FTMON::Base;
use FTMON::Product;
use FTMON::Environment;
use FTMON::ConfigFileParser;
use FTMON::Monitor;
use FTMON::Scheduler;
use FTMON::EventManager;
use FTMON::EsculationPolicy;

# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
$DEBUG = 0 if ( ! defined($FTMON::ConfigFile::DEBUG) );

@FTMON::ConfigFile::ISA = ("FTMON::Base");

$_LAST_ATTRIB = $FTMON::Base::_LAST_ATTRIB + 13;

my(
   # The Monitor resulting from compiling the ConfigFile.
   $MONITOR, 

   # The Name of the configuration files.
   $NAME, 

   # The Variables resulting from Compiling the ConfigFile.
   $VARIABLES, 

   # The Thresholds resulting from Compiling the ConfigFile.
   $THRESHOLDS, 

   # The Include file directives resulting from Compiling the ConfigFile.
   $INCLUDES, 

   # Variables extracted from the Implementation file.
   # Hash of variable comments, default values indexed by the variable name.
   $IMPL_VARIABLES, 
   $IMPL_DESCRIPTION, 
   $PARSED, 
   $MONITOR_NAME,
   $STR, 
   $MD5, 
   $COMPILE_TIME, 
   $LAST_LINE, 
   ) = 
      ( $FTMON::Base::_LAST_ATTRIB + 1 .. $_LAST_ATTRIB );

# Class methods
 



# Constructor
# ----------------------------------------------------------------------
sub new
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $proto        = shift;
  my $name         = shift;
  my $str          = shift;

  my $class = ref($proto)  || $proto;

  $DEBUG && TraceFuncs::debug("Create new instance");
  $self = $class->SUPER::new($name);
  bless($self, $class);

  $self->[$NAME] = (defined($name)) ? $name : undef;

  $self->[$STR]          = (defined($str)) ? $str : undef;
  $self->[$LAST_LINE]    = 0;

  $self->[$MD5] = "";
  $self->[$VARIABLES]    = [];
  $self->[$IMPL_VARIABLES] = {};
  $self->[$IMPL_DESCRIPTION] = "";
  $self->[$INCLUDES]     = [];
  $self->[$THRESHOLDS]   = [];
  $self->[$MONITOR]      = undef;
  $self->[$MONITOR_NAME] = undef;

  return($self);
}

$SINGLETON = FTMON::ConfigFile->new();
$MERGE = FTMON::ConfigFile->new("merge");

# ----------------------------------------------------------------------
# MD5 encryption is used to determine if the configuration file has changed
# and needs to be reloaded or not.
# ----------------------------------------------------------------------
sub md5
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;

  return($self->[$MD5]);
}

# ----------------------------------------------------------------------
# When config file was last compiled.
# ----------------------------------------------------------------------
sub compile_time
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;

  return($self->[$COMPILE_TIME]);
}

# ----------------------------------------------------------------------
sub hex_digest
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;

  return($self->[$MD5]);
}

# ----------------------------------------------------------------------
sub description
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;

  return($self->[$IMPL_DESCRIPTION]);
}


# ----------------------------------------------------------------------
sub DESTROY
{
  my $self = shift;
  $DEBUG && TraceFuncs::trace(my $f);

  my $name = $self->name();

  $self->SUPER::DESTROY();
}


# --------------
sub variable_data
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  my $variable_name = shift;

  my $variable_data = [];
  $variable_name =~ s/^\s+//;
  $variable_name =~ s/\s+$//;
  $variable_name =~ s/^\$//;

  $variable_data = $self->[$IMPL_VARIABLES]->{$variable_name}
      if ( defined($self->[$IMPL_VARIABLES]->{$variable_name}) );

  return $variable_data;
}

# --------------
sub variable_names
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  my @variable_names;

  return sort keys %{$self->[$IMPL_VARIABLES]};
}



 # -------------------------------------------------------------------------
sub rs_parsed
{
  my $self = shift;

  if (@_) 
  {
    $self->[$PARSED] = shift;
  }

  return($self->[$PARSED]);
}



# ----------------------------------------------------------------------
sub load
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;



  if ( ! defined $self->rs_parsed() )
  {
    # REVIST;
    die "Did not parse configuration file: " . $self->name();
    return;
  }

  eval
  {
    &{$self->rs_parsed()}();
  };
  die $self->name() . ": " . $@ if ( $@ );
}


# ----------------------------------------------------------------------
# Compiles the associated configuration file. This is the main function
# of this module.
# ----------------------------------------------------------------------
sub compile
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;

  my $existing_monitor = 0;


  $FT::MONITOR::NAME = undef;
  $FT::VENDOR = undef;
  $FT::PRODUCT = undef;
  $FT::PACKAGE = undef;

  $DEBUG && TraceFuncs::debug("compile: " . $self->name() );
  $self->[$COMPILE_TIME] = time();

  my $esculation_policy_sub = "";
  my $monitor;


  #
  # Send off to config file parser for parsing. 
  #
  my $parsed = "";

  # Try to stop memory leaks
  $self->[$THRESHOLDS] = undef;
  $self->[$INCLUDES] = undef;
  $self->[$VARIABLES] = undef;


  @FT::THRESHOLD = ();
  @FT::INTERP = ();
  %FT::INFO = ();

  %EP::SEV = ();
  %EP::MSG = ();

  $self->[$LAST_LINE]    = 0;

  # =====================================================================
  # 1st Pass:  Parse the configuration file.
  # =====================================================================
  my $md5;
  my $description;
  my $status = $FTMON::ConfigFileParser::SINGLETON->parse(
                  $self, \$md5, 
                  $self->[$IMPL_VARIABLES], \$description);
  $self->[$IMPL_DESCRIPTION] = $description;
  $self->[$MD5] = $md5;



  # =====================================================================
  # 2nd Pass:  Create the monitor/change thresholds
  # =====================================================================
  my $product;
  my $product_name;


  #
  # Check if monitor already exists.
  # NB You cannot change monitor implementations without first restarting FTMON.
  #
  if ( defined($self->monitor()) )
  {
    $DEBUG && TraceFuncs::debug("Using existing monitor");

    $monitor = $self->monitor();
    $existing_monitor = 1;
  }
  elsif ( defined($FT::PACKAGE) )
  {
    if ( ! $existing_monitor )
    {
      $monitor = FTMON::Monitor->new(
	            $FT::PACKAGE,
		    $self,
	            $FT::MONITOR::DESC,
	            $FT::MONITOR::VER);

      $product_name = $FT::VENDOR . "::" . $FT::PRODUCT;
      $product = $FTMON::Environment::SINGLETON->find_product($product_name);
      if ( ! defined $product )
      {
        $product = FTMON::Product->new(
            $FT::VENDOR . "::" . $FT::PRODUCT,
            $FT::PRODUCT::DESC,
            $FT::PRODUCT::SUMMARY,
            $FT::PRODUCT::CONTACT);

        $FTMON::Environment::SINGLETON->add_product($product);
      }

      $product->add_monitor($monitor);
      $self->[$MONITOR] = $monitor;
    }
  }
  else
  {
    die "No FT::PACKAGE defined for " . $self->name() . "\n";
  }


  #
  # Check Monitor Subroutines are all defined as required.
  #
  if ( ! $exiting_monitor )
  {
    my $description = ( defined($FT::MONITOR::DESC) ) 
                           ? $FT::MONITOR::DESC
			     : "Undocumented monitor";
    my $version = ( defined($FT::MONITOR::VER) ) 
                           ? $FT::MONITOR::VER
			     : "unknown";
    $monitor->description($description);
    $monitor->version($FT::MONITOR::VER);


    $DEBUG && TraceFuncs::debug("New monitor");

    die "You must define FT::MONITOR::PRECALCS"
        if ( ! defined(&FT::MONITOR::PRECALCS) );
    $monitor->rs_monitor(\&FT::MONITOR::PRECALCS);

    die "You must define FT::MONITOR::COLS"
      if ( ! defined &FT::MONITOR::COLS);
    $monitor->rs_assign_row(\&FT::MONITOR::COLS);

    die "You must define FT::MONITOR::SCHED"
      if ( ! defined &FT::MONITOR::SCHED);
    $monitor->rs_sched(\&FT::MONITOR::SCHED);

    die "You must define FT::MONITOR::VARIABLES"
       if ( ! defined &FT::MONITOR::VARIABLES );
    $monitor->rs_variables(\&FT::MONITOR::VARIABLES);
    
    die "You must define FT::MONITOR::VARIABLES_INIT"
       if ( ! defined &FT::MONITOR::VARIABLES_INIT );
    $monitor->rs_variables_impl(\&FT::MONITOR::VARIABLES_INIT);

    # Post subroutines are usefull for sorting INFO displays.
    die "You must define FT::MONITOR::POSTCALCS"
       if ( ! defined &FT::MONITOR::POSTCALCS );
    $monitor->rs_emonitor(\&FT::MONITOR::POSTCALCS);

    die "You must define FT::MONITOR::CALCS"
       if ( ! defined &FT::MONITOR::CALCS );
    $monitor->rs_precalc(\&FT::MONITOR::CALCS);


    FT::MONITOR::VARIABLES_INIT();
    FT::MONITOR::VARIABLES();

    #
    # Schedule the monitor.
    #
    my $hostname = ( defined($FT::MONITOR::HOST) ) 
                           ? $FT::HOSTNAME
			     : "Undocumented monitor";
    my $monitor_link = $FT::VENDOR . "/" . $FT::PRODUCT . "/" . 
                       $FT::MONITOR::NAME . ".html";
    $monitor_link =~ s/::/_/g;

    my $monitor_name = $monitor->name();
    # REVISIT: Why need hostname.
    my $job = FTMON::Job->new(
	   "MONITOR: <a href=\"$monitor_link\">$monitor_name</a>",
           $monitor->rs_sched(),
           sub { $monitor->run() },
	   $hostname,
	   $description);
	   
    $FTMON::Scheduler::SINGLETON->at($job);

  }


  die "You must define FT::MONITOR::THRESHOLDS"
     if ( ! defined(&FT::MONITOR::THRESHOLDS) );
  $monitor->rs_threshold(\&FT::MONITOR::THRESHOLDS);


  # REVISIT: Need this?
  $FT::MERGE = 0;

  return(1);
}




# ----------------------------------------------------------------------
sub backup
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;

  my $backup_file = $self->name();
  $backup_file =~ s/\.cfg$/\.bak/;
  FTMON::Helper::file_copy($self->name(), $backup_file);
}


# ----------------------------------------------------------------------
sub add_variable
{
  my $self = shift;
  my $variable = shift;

  push( @{$self->[$VARIABLES]}, $variable );
}

# ----------------------------------------------------------------------
sub add_include
{
  my $self = shift;
  my $include = shift;

  push( @{$self->[$INCLUDES]}, $include );
}

# ----------------------------------------------------------------------
sub add_threshold
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  my $threshold = shift;

  die "Threshold config not defined" if ( ! defined $threshold );

  push( @{$self->[$THRESHOLDS]}, $threshold );
}


# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# by string.
sub merge_config_str
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  my $config_str = shift;

  $self->backup();

  my $file_name = $self->name();
  $new_file_name = $file_name;
  $new_file_name =~ s/\.cfg/\.new/;

  open(CFG,  "$file_name" ) ||
	     die "Could not open" . $file_name . ": " . $!;

  open(DUMP,  "> $new_file_name" ) ||
	     die "Could not open" . $new_file_name . ": " . $!;
  my $thresholds = $self->thresholds();
  while ( <CFG> )
  {
    if ( /sub FT::MONITOR::THRESHOLDS/ )
    {
      print DUMP "sub FT::MONITOR::THRESHOLDS\n{\n[\n";
      foreach $threshold (@$thresholds)
      {
        print DUMP $threshold->str();
      }
      print DUMP $FT::CONFIG_STR;
      print DUMP "];\n};\n";
      print DUMP 'print "SOURCED: " . __FILE__ . "\n";';
      print DUMP "\n";

      last;
    }
    else
    {
      print DUMP $_;
    }
  }
  close (CFG);
  close (DUMP);

  unlink($file_name);
  rename($new_file_name, $file_name);

}




# ----------------------------------------------------------------------
# by string.
sub merge_config
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  my $config_file = shift;

  my $includes   = [];
  my $variables  = [];
  my $thresholds = [];


  #
  # Ensure we have the the monitor names and include files of the config
  # file we are merging with.
  #
  &{$self->monitor()->rs_environment()}();

  my $pre_str = "";
  $pre_str = $pre_str . 
           "\$FT::VENDOR = \"" . $FT::VENDOR . "\";\n";
  $pre_str = $pre_str . 
           "\$FT::PRODUCT = \"" . $FT::PRODUCT . "\";\n";
  $pre_str = $pre_str . 
           "\$FT::MONITOR::NAME = \"" . $FT::MONITOR::NAME . "\";\n";

  $includes = $self->includes();
  foreach $current ( @{$includes} )
  {
    $pre_str = $pre_str . $current->config_str();
  }

  my $status = $config_file->compile($pre_str);
  

  my $variable;
  my $include;
  my $threshold;

  my $last_line;
  my $current;
  my $name;

  $includes = $config_file->includes();
  foreach $current ( @{$includes} )
  {
    $name = $current->objid();
    $name = $current->name();
    if ( ( $include = $self->find_include($name) ) )
    {
      # Replace previous variable.
	$include->config_str($current->config_str());
    }
    else
    {
      # Add new variables to the end.

      $include = 
          FTMON::ConfigFile::Include->new(
             $current->name(), 
	       undef,
	       $current->config_str() );
      
      $self->add_include($include);

    }
  }


  $variables = $config_file->variables();
  foreach ( @{$variables} )
  {
    next if ( $_->name() eq "FT::MONITOR::NAME");

    if ( $variable = $self->find_variable($_->name(), $_->config_str()) )
    {
      # Replace previous variable.
	$variable->config_str($_->config_str());
    }
    else
    {
      next if ( $_->name() eq 'FT::VENDOR' );
      next if ( $_->name() eq 'FT::PRODUCT' );
      next if ( $_->name() eq 'FT::MONITOR::NAME' );
      # Add new variables to the end.
      $variable = 
          FTMON::ConfigFile::Variable->new(
             $_->name(), 
	       undef,
	       $_->config_str() );
      $self->add_variable($variable);
    }
  }

  
  $thresholds = $config_file->thresholds();
  my $merge_threshold;
  my $i = 0;
  foreach $merge_threshold ( @{$thresholds} )
  {
    if ( ( $threshold = $self->find_threshold($merge_threshold) ) )
    {
      # Replace previous thresholds.
        $threshold->initialise($merge_threshold->duplicate());
	$threshold->config_str($merge_threshold->config_str());
    }
    else
    {
        # Add new threshold to the end.
        $threshold = 
          FTMON::ConfigFile::Threshold->new(
	       $merge_threshold->config_str());
	       
          $threshold->initialise($merge_threshold->initialise());
	  $self->add_threshold($threshold);
    }
    $i ++;
  }
}



# ----------------------------------------------------------------------
sub find_variable
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  my $variable_name = shift;
  my $config_str = shift;

  
  my $variable = undef;
  foreach ( @{$self->[$VARIABLES]} ) 
  {
    print STDERR $_->config_str(), "|", $config_str, "\n";
    if ( $_->config_str() eq $config_str ||
         $_->name() eq $variable_name )
    {
      $variable = $_;
      $DEBUG && TraceFuncs::debug("found match for $config_str");
	last;
    }
  }

  return($variable);
}

# ----------------------------------------------------------------------
sub find_match_variable
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  my $variable_name = shift;

  
  my $variable = undef;
  foreach ( @{$self->[$VARIABLES]} ) 
  {
    if ( $_->name() eq $variable_name )
    {
      $variable = $_;
	last;
    }
  }

  return($variable);
}

# ----------------------------------------------------------------------
sub find_include
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  my $include_name = shift;
  
  my $include = undef;
  my $current = undef;
  foreach $current ( @{$self->[$INCLUDES]} ) 
  {
    if ( $current->name() eq $include_name )
    {
      $include = $current;
	last;
    }
  }

  return($include);
}

# ----------------------------------------------------------------------
sub find_match_include
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  my $include_name = shift;

  
  my $include = undef;
  foreach ( @{$self->[$INCLUDES]} ) 
  {
    if ( $_->name() eq $include_name )
    {
      $include = $_;
	last;
    }
  }

  return($include);
}


# ----------------------------------------------------------------------
sub find_threshold
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  my $merge_threshold = shift;

  my $threshold_resource = $merge_threshold->resource();
  my $threshold_eventid = $merge_threshold->eventid();
  my $threshold_severity = $merge_threshold->severity();
  my $threshold_str = $merge_threshold->config_str();

  
  my $current_severity;
  die "find_threshold(): threshold_str not defined" 
     if ( ! defined $threshold_str || ! $threshold_str );

  $threshold_str =~ s/\s//g;

  my $threshold = undef;
  $DEBUG && TraceFuncs::debug("compare : " . 
                               "config_str:" . $threshold_str . "\n" .
                               "resource:" . $threshold_resource . "\n" .
                               "event_id:" . $threshold_eventid);
  foreach $current ( @{$self->[$THRESHOLDS]} ) 
  {
    # REVISIT - Why don't comparisons work !
    my $current_severity = $current->severity();
    my $current_threshold_str = $current->config_str();
    $current_threshold_str =~ s/\s//g;

    my $compare_sev = 0;
    $compare_sev = ( $current_severity->cmp($threshold_severity) )
        if ( defined $current_severity );

    $DEBUG && TraceFuncs::debug("severity : " . 
                               "config_str:" . $current->config_str() . "\n" .
                               "resource:" . $current->resource() . "\n" .
                               "eventid:" . $current->eventid() . "\n" .
			         "compare=" . $compare);
    if ( 
         $current_threshold_str eq $threshold_str ||
         (
	   $current->resource() eq $threshold_resource &&
           $current->eventid() eq $threshold_eventid &&
           $compare_sev 
         )
       )
    {
	   #&&
	   #( 
           #  ( $current->config_str() =~ /FT::BL/ && 
	   #$threshold_str =~ /FT::BL/ ) ||
           #
           #  ( $current->config_str() !~ /FT::BL/ && 
	   # $threshold_str !~ /FT::BL/ )    
	   #)
	   
      $DEBUG && TraceFuncs::debug(
	  "match " . $threshold_resource . " " .
	  $threshold_eventid . " " . $threshold_severity->str() );
        $threshold = $current;
	last;
    }
  }

  return($threshold);
}

# ----------------------------------------------------------------------
sub find_match_threshold
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  my $variable_name = shift;

  
  my $variable = undef;
  foreach ( @{$self->[$VARIABLES]} ) 
  {
    if ( $_->name() eq $variable_name )
    {
      $variable = $_;
	last;
    }
  }

  return($variable);
}

# ----------------------------------------------------------------------
sub name
{
  my $self = shift;
  if (@_) 
  {
    $self->[$NAME] = shift;
  }
  return($self->[$NAME]);
}


# ----------------------------------------------------------------------
sub threshold_index
{
  my $self = shift;
  my $index = ( @{$self->[$THRESHOLDS]} - 1 );
  return $index;
}

# ----------------------------------------------------------------------
sub last_line
{
  my $self = shift;
  if (@_) 
  {
    $self->[$LAST_LINE] = shift;
  }
  return($self->[$LAST_LINE]);
}

# ----------------------------------------------------------------------
sub variables
{
  my $self = shift;
  if (@_) 
  {
    $self->[$VARIABLES] = shift;
  }
  return($self->[$VARIABLES]);
}

# ----------------------------------------------------------------------
sub includes
{
  my $self = shift;
  if (@_) 
  {
    $self->[$INCLUDES] = shift;
  }
  return($self->[$INCLUDES]);
}


# ----------------------------------------------------------------------
sub thresholds
{
  my $self = shift;
  if (@_) 
  {
    $self->[$THRESHOLDS] = shift;
  }
  return($self->[$THRESHOLDS]);
}


# ----------------------------------------------------------------------
sub monitor
{
  my $self = shift;
  if (@_) 
  {
    $self->[$MONITOR] = shift;
  }
  return($self->[$MONITOR]);
}

# ----------------------------------------------------------------------
sub monitor_name
{
  my $self = shift;
  if (@_) 
  {
    $self->[$MONITOR_NAME] = shift;
  }

  return($self->[$MONITOR_NAME]);
}


# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
{
package FTMON::ConfigFile::Variable;
@FTMON::ConfigFile::Variable::ISA = ("FTMON::Base");

$_LAST_ATTRIB = $FTMON::Base::_LAST_ATTRIB + 5;
my($NAME, 
   $LINE, 
   $COMMENT, 
   $DESCRIPTION,
   $CFG_STR, ) = 
      ( $FTMON::Base::_LAST_ATTRIB + 1 .. $_LAST_ATTRIB );

# ----------------------------------------------------------------------
sub new
{ 
  $DEBUG && TraceFuncs::trace(my $f);
  my $proto       = shift;
  my $name        = shift;
  my $line        = shift;
  my $config_str  = shift;
  my $comment     = shift;

  $FT::MERGE = 0 if ( ! defined $FT::MERGE );
  $FT::PACKAGE = "" if ( ! defined $FT::PACKAGE );

  die "No FT::PACKAGE defined" if ( ! $FT::PACKAGE );
  my $id = "VARIABLE_" . $FT::MERGE . "_" . $FT::PACKAGE . "_" . $name;
  #if ( $FTMON::Base::SINGLETON->find_instance($class, $id) )
  #{
  #  die "Duplicate instances of $class $name exist for $FT::PACKAGE " .
  #      "- line $line";
  #}
  my $class = ref($proto)  || $proto;
  my $self = [];

  # Use existing instance if one exists.
  if ( ! ( $self = $FTMON::Base::SINGLETON->find_instance($class, $id )) )
  {
    $DEBUG && TraceFuncs::debug("Create new instance.");
    $self = $class->SUPER::new($id);
    bless($self, $class);
  }
  
  $self->[$NAME]        = (defined($name)) ? $name : undef;
  $self->[$LINE]        = (defined($line)) ? $line : undef;
  $self->[$CFG_STR]     = (defined($config_str)) ? $config_str : undef;
  $self->[$COMMENT]     = (defined($comment)) ? $comment : undef;
  $self->[$DESCRIPTION] = "tbd";

  return($self);
}

# ----------------------------------------------------------------------
sub DESTROY
{
  my $self  = shift;
  $self->SUPER::DESTROY();
}


# ----------------------------------------------------------------------
sub name 
{
  my $self = shift;
  if (@_) 
  {
    $self->[$NAME] = shift;
  }
  return($self->[$NAME]);
}


# ----------------------------------------------------------------------
sub line 
{
  my $self = shift;
  if (@_) 
  {
    $self->[$LINE] = shift;
  }
  return($self->[$LINE]);
}

# ----------------------------------------------------------------------
sub config_str 
{
  my $self = shift;
  if (@_) 
  {
    $self->[$CFG_STR] = shift;
  }
  return($self->[$CFG_STR]);
}

# ----------------------------------------------------------------------
sub comment 
{
  my $self = shift;
  if (@_) 
  {
    $self->[$COMMENT] = shift;
  }
  return($self->[$COMMENT]);
}

};

# ----------------------------------------------------------------------
# ----------------------------------------------------------------------

{
package FTMON::ConfigFile::Include;

@FTMON::ConfigFile::Include::ISA = ("FTMON::Base");

$_LAST_ATTRIB = $FTMON::Base::_LAST_ATTRIB + 5;
my($NAME, 
   $LINE, 
   $COMMENT, 
   $DESCRIPTION,
   $CFG_STR,) = 
      ( $FTMON::Base::_LAST_ATTRIB + 1 .. $_LAST_ATTRIB );

# ----------------------------------------------------------------------
sub new
{ 
  $DEBUG && TraceFuncs::trace(my $f);
  my $proto       = shift;
  my $name        = shift;
  my $line        = shift;
  my $config_str  = shift;
  my $comment     = shift;

  $FT::MERGE = 0 if ( ! defined $FT::MERGE );
  $FT::PACKAGE = "" if ( ! defined $FT::PACKAGE );

  my $id = "INCLUDE_" . $FT::MERGE . "_" . $FT::PACKAGE . "_" . $name;
  #if ( $FTMON::Base::SINGLETON->find_instance($class, $id) )
  #{
  #  die "Duplicate instances of INCLUDE file '$id' LINE $line";
  #}
  my $class = ref($proto)  || $proto;
  my $self = [];

  # Use existing instance if one exists.
  if ( ! ( $self = $FTMON::Base::SINGLETON->find_instance($class, $id )) )
  {
    $DEBUG && TraceFuncs::debug("Create new instance.");
    $self = $class->SUPER::new($id);
    bless($self, $class);
  }
  
  $self->[$NAME]        = (defined($name)) ? $name : undef;
  $self->[$LINE]        = (defined($line)) ? $line : undef;
  $self->[$CFG_STR]     = (defined($config_str)) ? $config_str : undef;
  $self->[$COMMENT]     = (defined($comment)) ? $comment : undef;
  $self->[$DESCRIPTION] = "tbd";

  return($self);
}

# ----------------------------------------------------------------------
sub DESTROY
{
  my $self  = shift;
  $self->SUPER::DESTROY();
}

# ----------------------------------------------------------------------
sub name 
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  if (@_) 
  {
    $self->[$NAME] = shift;
  }
  $DEBUG && TraceFuncs::trace("name = " . $self->[$NAME]);
  return($self->[$NAME]);
}


# ----------------------------------------------------------------------
sub line 
{
  my $self = shift;
  if (@_) 
  {
    $self->[$LINE] = shift;
  }
  return($self->[$LINE]);
}

# ----------------------------------------------------------------------
sub config_str
{
  my $self = shift;
  if (@_) 
  {
    $self->[$CFG_STR] = shift;
  }
  return($self->[$CFG_STR]);
}

# ----------------------------------------------------------------------
sub comment 
{
  my $self = shift;
  if (@_) 
  {
    $self->[$COMMENT] = shift;
  }
  return($self->[$COMMENT]);
}


};

# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
{
package FTMON::ConfigFile::Threshold;

@FTMON::ConfigFile::Threshold::ISA = ("FTMON::Base");

my ($RESOURCE,
    $CALCULATION,
    $SEVERITY,
    $EVENT_ID,
    $MESSAGE,
    $ACTION, ) = ( 0 .. 5 );

$_LAST_ATTRIB = $FTMON::Base::_LAST_ATTRIB + 6;
my( $REF, 
    $STR,
    $SUB_STR,
    $SEVERITY_OBJ,
    $MESSAGE_OBJ,
    $ACTION_OBJ,
   ) =
      ( $FTMON::Base::_LAST_ATTRIB + 1 .. $_LAST_ATTRIB );

sub new
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $proto = shift;
  my $str = shift;


  # my $id = "THRESHOLD_" . $FT::MERGE . "_" . $FT::PACKAGE . "_" . $ref;
  my $id = "THRESHOLD_" . $FT::MERGE . "_" . $FT::PACKAGE . "_" . $str;
  $id =~ s/\s//g;

  #if ( $FTMON::Base::SINGLETON->find_instance($class, $id) )
  #{
  #  print STDERR "Duplicate instances of $class $config_str for $FT::PACKAGE " .
  #      "at LINE $line \n";
  #}
  my $class = ref($proto)  || $proto;
  my $self = [];

  # Use existing instance if one exists.
  if ( ! ( $self = $FTMON::Base::SINGLETON->find_instance($class, $id )) )
  {
    $DEBUG && TraceFuncs::debug("Create new instance.");
    $self = $class->SUPER::new($id);
    bless($self, $class);
  }
  
  
  $self->[$STR] = undef;
  $self->str($str) if (defined $str);

  $self->[$REF] = undef;

  return($self);
}

# ----------------------------------------------------------------------
sub DESTROY
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self  = shift;

  $self->SUPER::DESTROY();
}

sub ref
{
  $self->[$REF] = shift;
}

# ----------------------------------------------------------------------
# Initialises the Threshold objects.
# ----------------------------------------------------------------------
sub initialise 
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;

  my $ref;
  $ref = $self->[$REF] = shift;
  my $index = shift;

  # REVISIT: undef calculations indicate skip resources.
  return if ( ! defined $self->[$REF]->[$CALCULATION] );

  die $FT::PACKAGE . ": No severity defined for " . $self->str()
       if  ( ! defined($ref->[$SEVERITY]) );

  if ( defined $self->[$SEVERITY_OBJ] )
  {
    $self->[$SEVERITY_OBJ]->policy($ref->[$SEVERITY]);
  }
  else
  {
    $self->[$SEVERITY_OBJ] = 
        FTMON::SeverityEsculationPolicy->new($ref->[$SEVERITY]);
  }

  die $FT::PACKAGE . ": No message defined for " . $self->str()
     if  ( ! defined($ref->[$MESSAGE]) );

  if ( defined $self->[$MESSAGE_OBJ] )
  {
    $self->[$MESSAGE_OBJ]->policy($ref->[$MESSAGE]);
  }
  else
  {
    $self->[$MESSAGE_OBJ] =  
        FTMON::MessageEsculationPolicy->new($ref->[$MESSAGE]);
  }

  if ( defined $ref->[$ACTION] )
  {
    if ( defined $self->[$ACTION_OBJ] )
    {
      $self->[$ACTION_OBJ]->policy($ref->[$ACTION]);
    }
    else
    {
      $self->[$ACTION_OBJ] =  
          FTMON::ActionEsculationPolicy->new($ref->[$ACTION]);
    }
  }
}

# ----------------------------------------------------------------------
# duplicates/gets the Threshold objects.
# ----------------------------------------------------------------------
sub duplicate 
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;

  return($self->[$REF]);
}


sub str 
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;

  my $str;

  if (@_) 
  {
    $str = $self->[$STR] = shift;

    $str =~ s/^\s+//;
    $str =~ s/^\[//;
    $str =~ s/\s+$//;
    $str =~ s/,$//;
    $str =~ s/\]$//;
    $str =~ s/\s+$//;
    my @sub_str = FT::parse_csv($str);
    $self->[$SUB_STR] = [ @sub_str ];
  }
  return($self->[$STR]);
}


# ----------------------------------------------------------------------
sub calculation 
{
  my $self = shift;
  return( $self->[$REF]->[$CALCULATION] );
}   

# ----------------------------------------------------------------------
sub calculation_str 
{
  my $self = shift;
  return( $self->[$SUB_STR]->[$CALCULATION] );
}   


# ----------------------------------------------------------------------
sub severity 
{
  my $self = shift;
  return( $self->[$SEVERITY_OBJ] );
}   
# ----------------------------------------------------------------------
sub severity_str 
{
  my $self = shift;
  return( $self->[$SUB_STR]->[$SEVERITY] );
}   


# ----------------------------------------------------------------------
sub resource 
{
  my $self = shift;
  return( $self->[$REF]->[$RESOURCE] );
}   

# ----------------------------------------------------------------------
sub resource_str 
{
  my $self = shift;
  return( $self->[$SUB_STR]->[$RESOURCE] );
}   


# ----------------------------------------------------------------------
sub eventid 
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  return( $self->[$REF]->[$EVENT_ID] );
}   

# ----------------------------------------------------------------------
sub eventid_str
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  return( $self->[$SUB_STR]->[$EVENT_ID] );
}   


# ----------------------------------------------------------------------
sub message 
{
  my $self = shift;
  return( $self->[$MESSAGE_OBJ] );
}   

# ----------------------------------------------------------------------
sub message_str
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  return( $self->[$SUB_STR]->[$MESSAGE] );
}   


# ----------------------------------------------------------------------
sub action 
{
  my $self = shift;
  return( $self->[$ACTION_OBJ] );
}   

# ----------------------------------------------------------------------
sub action_str
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  return( $self->[$SUB_STR]->[$ACTION] );
}   

};

1;
  
__END__;

######################## User Documentation ##########################

## To format the following documentation into a more readable format,
## use one of these programs: perldoc; pod2man; pod2html; pod2text.
## For example, to nicely format this documentation for printing, you
## may use pod2man and groff to convert to postscript:

=head1 NAME

FTMON::ConfigFile - 

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
