package FTMON::ConfigFileParser;
#############################################################################
#                    FTMON (Fast Track Systems Monitor)
#
#   Script: @(#) $RCSfile: ConfigFileParser.pm,v $ 
# 
#   DESCRIPTION: 
#
#   @(#) Parses a configuration file.
#
#   $Source: /cvsroot/ftmon/base2/lib/FTMON/ConfigFileParser.pm,v $
#
#   $Date: 2003/01/10 13:10:39 $
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

use FTMON::Base;
use FTMON::Monitor;
use Digest::MD5;
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
$DEBUG = 0 if ( ! defined($FTMON::ConfigFileParser::DEBUG) );

@FTMON::ConfigFileParser::ISA = ("FTMON::Base");

my $VARIABLE_DEFN_REGEX     = '^\$(.*)=(.*;)$';

my $VARIABLE_START_REGEX    = '^\$(.*)=\s*$';
my $VARIABLE_CONTINUE_REGEX = '^\.+(.*)$';

my $THRESHOLD_REGEX         = '^\s*\[.*,.*\]';
my $INCLUDE_REGEX           = '^do\s+(.*);\s*$';

$_LAST_ATTRIB = $FTMON::Base::_LAST_ATTRIB + 2;
my(
  # Hash of config files parsed so far. ConfigFile objects indexed
  # by their names.
  $CONFIG_FILES,

  # Pointer of current variable being parsed.
  # ConfigFile parser attempts to record variable values and comments.
  $LAST_PARSE_VARIABLE,
  ) = ( $FTMON::Base::_LAST_ATTRIB + 1 .. $_LAST_ATTRIB );

# Constructor
# ----------------------------------------------------------------------
sub new
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $proto  = shift;

  my $class = ref($proto)  || $proto;
  my $self = $class->SUPER::new("ConfigFileParser");

  $self->[$LAST_PARSE_VARIABLE] = undef;

  $self->[$CONFIG_FILES] = {};

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
sub list_config_files
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  my $list = shift;

  my $row = [];
  my $name;
  my $config_file;
  my $num_thresholds;
  my $thresholds;
  my $num_variables;
  my $variables;
  my $num_includes;
  my $includes;
  my @config_files;
  while ( ($name, $config_file) = each %{$self->[$CONFIG_FILES]} )
  {
    $DEBUG && TraceFuncs::debug($name);
    $variables = $config_file->variables();
    $thresholds = $config_file->thresholds();
    $includes = $config_file->includes();

    $num_variables  = @{$variables};
    $num_thresholds = @{$thresholds};
    $num_includes   = @{$includes};
    $row = [ $name, $num_variables, $num_thresholds, $num_includes];
    push(@{$list}, $row);
  }

}

# ----------------------------------------------------------------------
sub find_config_file_instance
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;

  my $config_file = shift;

  if ( defined($self->[$CONFIG_FILES]->{$config_file}) )
  {
    $DEBUG && TraceFuncs::debug(
       "return " .
       $self->[$CONFIG_FILES]->{$config_file}->name());

    return($self->[$CONFIG_FILES]->{$config_file});
  }
  else
  {
    $DEBUG && TraceFuncs::debug("return undef");
    return(undef);
  }
}

# ----------------------------------------------------------------------
sub find_monitor_instance
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;

  my $monitor_name = shift;

  my $config_file;
  my $ref;
  while ( ( $config_file, $ref ) = each %{$self->[$CONFIG_FILES]} )
  {
    my $monitor = $ref->monitor();
    next if ( ! defined $ref->monitor() );
    if ( $monitor->monitor_name() eq $monitor_name )
    {
      return $monitor;
    }
  }
  return 0;
}


# ----------------------------------------------------------------------
# parse_impl attepts to record variable comments and help strings for
# "self documenting" monitors.
# ----------------------------------------------------------------------
sub parse_impl
{
  $DEBUG && TraceFuncs::trace(my $f);

  my $self = shift;
  my $file = shift;
  my $variables  = shift;
  my $description  = shift;

  my $impl_file = $file;
  $impl_file =~ s/\/([\w_]*)\.cfg/\/impl\/$1\.cfg/;

  my $source = "";
  my $variable;
  my $variable_type;
  my $value;
  my $comment = "";

  $$description = "";
  open(FILE, "< $impl_file") || die "Could not open '$impl_file': $1";
  $comment = "";
  while ( <FILE>  )
  {
    chomp;
    s/^\s*//;
    s/\s*$//;
    if ( /^#(.*)$/ )
    {
      $comment = $comment . " " . $1;
    }
    else
    {
      $$description = $comment;
      $comment = "";
      last;
    }
  }



  while ( <FILE> )
  {
    chomp;
    s/^\s*//;
    s/\s*$//;
    if ( /^#(.*)$/ )
    {
      $comment = $comment . " " . $1;
    }
    elsif ( /^$/ )
    {
      $comment = "";
    }
    elsif ( /^\$(\S+)\s*=\s*(.*);$/ )
    {
      $variable = $1;
      $value = $2;

      next if ( $variable !~ /(_P|_V|_A|_M|_I)$/ &&
                $variable !~ /^FT/ );

      next if ( $variable =~ /FT::PRODUCT$/ );
      next if ( $variable =~ /FT::VENDOR/ );
      next if ( $variable =~ /FT::PACKAGE/ );
      next if ( $variable =~ /FT::MONITOR::/ );
      next if ( $variable =~ /FT::PRODUCT::SUMMARY/ );
      next if ( $variable =~ /FT::PRODUCT::DESC/ );
      next if ( $variable =~ /FT::INTERP/ );

      if ( $value =~ /^\'.*\'$/ )
      {
        $variable_type = "string";
	$value = $1;
      }
      elsif ( $value =~ /^\"(.*)\"$/ )
      {
        $variable_type = "string";
	$value = $1;
      }
      elsif ( $value =~ /^[\d+]*\.[\d]*$/ )
      {
        $variable_type = "float";
      }
      elsif ( $variable =~ /::DEBUG/ || $variable =~ /::IS_/ )
      {
        $variable_type = "bool";
      }
      elsif ( $value =~ /^[\d+]+$/ )
      {
        $variable_type = "integer";
      }
      elsif ( $value =~ /^\[.*\]$/ )
      {
        $variable_type = "array";
      }

      #$comment = $comment . " DEFAULT_VALUE: $value, TYPE: $variable_type ";
      $variables->{$variable} = [$value, $comment, $variable_type];
      $comment = "";
    }
  }
  close(FILE);
}

# ----------------------------------------------------------------------
# Parses the specified configuration file.
# This is the main function in this module.
# ----------------------------------------------------------------------
sub parse 
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  my $config_file = shift;
  my $md5 = shift;
  my $impl_variables = shift;
  my $impl_description = shift;

  $self->[$LAST_PARSE_VARIABLE] = undef;

  if ( ! defined($config_file->name()) || ! $config_file->name() )
  {
    die "You must define a name for the configuration file";
  }

  my $cfg_file = $cfg_file_name = $config_file->name();

  # Add to list of configuration files compiled so far.
  $self->[$CONFIG_FILES]->{$cfg_file} = $config_file;

    
  my $comment = "";
  my $variable;
  my $value;
  my $source = "";
  my @lines = ();
  my $line = "";
  my $monitor_dir =  "";

  die "$cfg_file does not exist or is not readable." if ( ! -f $cfg_file );
  
  
  # Extract out variable comments etc.
  $self->parse_impl($cfg_file, $impl_variables, $impl_description);

  
  # Calculate checksum
  open(CFG, $cfg_file ) ||
    die "Could not open file $cfg_file - $!";

  binmode(CFG);
  $$md5 = Digest::MD5->new->addfile(*CFG)->hexdigest();
  close(CFG) || 
    die "Could not close file $cfg_file - $!";

  open(CFG, $cfg_file ) ||
    die "Could not open file $cfg_file - $!";
  while ( <CFG> )
  {
    chomp;

    push(@lines, $_);
  }

  close(CFG) || 
    die "Could not close file $cfg_file - $!";

  $monitor_dir = $cfg_file;
  if( $monitor_dir =~ /(.*)[\\\/](.*)/)
  {
    $monitor_dir = $1;
    $cfg_file_name = $2;
  }
  else
  {
    $monitor_dir = cwd();
  }

  $DEBUG && TraceFuncs::debug("chdir($monitor_dir)\n");
  chdir($monitor_dir)
   || die $config_file->name() .
          ": could not change to monitor directory ($monitor_dir)";

  do "$cfg_file_name";

            
  ( $FT::VENDOR, $FT::PRODUCT, $FT::MONITOR::NAME ) = split("::", $FT::PACKAGE);

  $DEBUG && TraceFuncs::debug("FT::PACKAGE = $FT::PACKAGE");

  die "FT::MONITOR::NAME not defined" 
    if (! defined $FT::MONITOR::NAME || ! $FT::MONITOR::NAME);

  die "FT::PRODUCT not defined" 
    if (! defined $FT::PRODUCT || ! $FT::PRODUCT);

  die "FT::VENDOR not defined" 
    if (! defined $FT::VENDOR || ! $FT::VENDOR);

  foreach (@lines)
  {
    $self->parse_line( $config_file, $_);
    $config_file->last_line( 1 + $config_file->last_line() );
  }

  return 1 ;
}



# ----------------------------------------------------------------------
sub parse_line
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self        = shift;
  my $config_file = shift;
  my $line        = shift;

  
  $_ = $line;

  my $variable_name;
  my $variable_value;
  my $variable;
  my $threshold;


  my $config_index = 0;

  if ( ! defined($line) )
  {
    die "You must define a line to parse";
  }


  chomp;

  s/^\s+//;
  s/\s+$//;

  if ( /$VARIABLE_START_REGEX/ )
  {

    $variable_name  = $1;

    $variable_name  =~ s/\s+$//;

    next if ( $variable_name =~ /^FT::/ );

    $variable = 
       FTMON::ConfigFile::Variable->new(
         $variable_name, 
	   undef,
	   "$_\n");


    $config_file->add_variable($variable);

  }
  elsif ( /$VARIABLE_CONTINUE_REGEX/ )
  {

    $variable_value  = $1;


    $self->[$LAST_PARSE_VARIABLE]->config_str(
        $self->[$LAST_PARSE_VARIABLE]->config_str() .
	  "." . $variable_value . "\n");
  }
  elsif ( /$VARIABLE_DEFN_REGEX/ )
  {

    $variable_name  = $1;
    $variable_value = $2;

    $variable_name  =~ s/\s+$//;
    $variable_value =~ s/^\s+$//;

    next if ( $variable_name =~ /^FT::/ );

    $variable = 
       FTMON::ConfigFile::Variable->new(
         $variable_name, 
	   undef,
	   "$_\n");


    $config_file->add_variable($variable);

    $self->[$LAST_PARSE_VARIABLE] = $variable;

  }
  elsif ( /$THRESHOLD_REGEX/ )
  {

    $threshold = 
       FTMON::ConfigFile::Threshold->new(
	      "$_\n",
              FT::MONITOR::THRESHOLDS()->[$config_index]);
    $config_index++;


    $config_file->add_threshold($threshold);

  }
  elsif ( /$INCLUDE_REGEX/ )
  {
    my $include_name  = $1;

    my $include = 
      FTMON::ConfigFile::Include->new(
         $include_name, 
	   undef,
	   "$_\n");



    $config_file->add_include($include);

  }
}

$SINGLETON = FTMON::ConfigFileParser->new();

1;

__END__;

######################## User Documentation ##########################

## To format the following documentation into a more readable format,
## use one of these programs: perldoc; pod2man; pod2html; pod2text.
## For example, to nicely format this documentation for printing, you
## may use pod2man and groff to convert to postscript:

=head1 NAME

FTMON::ConfigFileParser - 

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
