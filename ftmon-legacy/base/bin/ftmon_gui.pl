#!/usr/local/bin/perl
#############################################################################
#                    FTMON GUI (Fast Track Systems Monitor GUI)
#
#   Script: @(#) $RCSfile: ftmon_gui.pl,v $ 
# 
#   DESCRIPTION: 
#
#   @(#) FTMON GUI provides an interface into the FTMON monitor
#   @(#) status and configuration.
#
#   $Source: /cvsroot/ftmon/base2/bin/ftmon_gui.pl,v $
#
#   $Date: 2003/01/10 13:09:48 $
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
$FT::VERSION = '@(#) $Revision: 1.1.1.1 $';
$FT::VERSION =~ s/[^\d.]//g;

use Wx;
use Wx::Grid;
use Wx::Html;

$FT::PACKAGE = "";
$FT::VENDOR = "";
$FT::PRODUCT = "";
$FT::MONITOR::NAME = "";
$FT::MONITOR::DESC = "";
$FT::MONITOR::VER = "";
$FT::MONITOR::SCHED = "";
$FT::FILE::DESC = "";

$FT::PRODUCT::SUMMARY = "";
$FT::PRODUCT::DESC = "";
$FT::PRODUCT::CONTACT = "";

Wx::InitAllImageHandlers();

#
# Determine location of FTMON configuration
#
if ( defined $ENV{'SystemRoot'} )
{
  my $winnt_cfg = $ENV{'SystemRoot'} . "/FTMON/ftmon.cfg";
  if ( -f "$winnt_cfg" )
  {
    do "$winnt_cfg";
  }
}
elsif ( -f "/etc/ftmon.cfg" )
{
  do "/etc/ftmon.cfg";
}


sub extractTable 
{
  my $file = shift;

  my $str;
  my $is_table = 0;

  if ( ! open( HTML, "$file" ) )
  {
    $str = "";
    return($str);
  }
  
  while ( <HTML> )
  {
    $is_table = 1 if ( /<TABLE/ );
    $str = $str . $_ if ( $is_table );
    if ( /\/TABLE>/ )
    {
      $str = $str . $_;
      $is_table = 0;
    }
  }
  close(HTML);

  return($str);
}


package MyFrame;

use strict;
use vars qw(@ISA);

@ISA = qw(Wx::Frame);

my( $ID_CFG, $ID_ABOUT, $ID_QUIT, 
    $ID_SAVE, $ID_HTML, $ID_SELECT, $ID_DESELECT, $ID_DESELECTALL,
    $ID_CELL_CHANGE, $ID_SELECT_CELL ) =
  ( 1 .. 100 );

my ($VARIABLE_SINGLE_QUOTES,
      $VARIABLE_DOUBLE_QUOTES,
      $VARIABLE_FLOAT,
      $VARIABLE_INTEGER,
      $VARIABLE_BOOL,
      $VARIABLE_ARRAY,
  ) = (1 .. 100);

use Wx qw(:treectrl :window :textctrl :sizer 
          wxDefaultPosition wxDefaultSize wxRED wxBLUE wxGREEN wxBITMAP_TYPE_ICO );


sub new {
  my $class = shift;
  my $this = $class->SUPER::new( undef, -1, $_[0], [ @_[1, 2] ],
                                 [ @_[3, 4] ] );

  my $bitmap_file = $main::BASE_DIR . "/ftmon_red.ico";
  my $icon = Wx::Icon->new($bitmap_file, wxBITMAP_TYPE_ICO, -1, -1 );
  $this->SetIcon( $icon );

  my $sizer = Wx::BoxSizer->new( wxVERTICAL );

  my $split1 = Wx::SplitterWindow->new($this, -1);
  my $split2 = Wx::SplitterWindow->new($split1, -1);
  
  $this->{TREECTRL} = MyTreeCtrl->new( $split1, -1 );

  $this->{TEXTCTRL} =
    Wx::TextCtrl->new( $split2, -1, '', wxDefaultPosition, wxDefaultSize,
                       wxTE_MULTILINE|wxSUNKEN_BORDER );

 $this->{NOTEBOOK} = Wx::Notebook->new($split2, -1);



  $split1->SplitVertically( $this->{TREECTRL}, $split2, 150);
  $split2->SplitHorizontally( $this->{NOTEBOOK}, $this->{TEXTCTRL}, 300);

  $this->{TEXTCTRL4} = Wx::HtmlWindow->new($this->{NOTEBOOK}, -1);
  $this->{NOTEBOOK}->AddPage( $this->{TEXTCTRL4}, "Help",1 );
  #$this->{TEXTCTRL4}->LoadPage("$main::HTML_DIR/ftmon.html");
  

  $this->{OLDLOG} = Wx::Log::SetActiveTarget
    ( Wx::LogTextCtrl->new( $this->{TEXTCTRL} ) );
    

  my $file = Wx::Menu->new;
  $file->Append( $ID_CFG, "&File" );
  $file->Append( $ID_ABOUT, "&About" );
  $file->AppendSeparator;
  $file->Append( $ID_QUIT, "E&xit" );

  my $item = Wx::Menu->new;
  $item->Append( $ID_SAVE, "Save" );
  $item->Append( $ID_HTML, "Html" );
  $item->AppendSeparator;
  $item->Append( $ID_SELECT, "Select" );
  $item->Append( $ID_DESELECT, "Deselect" );
  $item->Append( $ID_DESELECTALL, "Deslect All" );

  my $bar = Wx::MenuBar->new;
  $bar->Append( $file, "&File" );
  $bar->Append( $item, "&Config" );

  $this->SetMenuBar( $bar );



  use Wx::Event qw(EVT_MENU EVT_UPDATE_UI EVT_TREE_SEL_CHANGED);

  EVT_MENU( $this, $ID_CFG, \&LoadCfg );
  EVT_MENU( $this, $ID_ABOUT, \&OnAbout );
  EVT_MENU( $this, $ID_QUIT, \&OnQuit );

  EVT_MENU( $this, $ID_SAVE, \&OnSave );
  EVT_MENU( $this, $ID_HTML, \&OnHtml );
  EVT_MENU( $this, $ID_SELECT, \&OnSelect );
  EVT_MENU( $this, $ID_DESELECT, \&OnDeselect );
  EVT_MENU( $this, $ID_DESELECTALL, \&OnDeselectAll );

  EVT_TREE_SEL_CHANGED( $this, $this->{TREECTRL}, \&OnSelChange );


  $this;
}


sub OnSelChange {
  my( $this, $event ) = @_;
  my $item = $event->GetItem;
  my $data;

  my %variables;
  Wx::LogMessage( 'Text: %s', $this->{TREECTRL}->GetItemText( $item ) );
  if( $data = $this->{TREECTRL}->GetItemData( $item ) ) {
    Wx::LogMessage( 'Data: %s', $data->GetData );
    my $data_value = $data->GetData;
    if ( -f $data_value )
    {
      $this->CheckFile($data_value);
      $this->LoadImplFile($data_value, \%variables);
      $this->LoadFile($data_value, \%variables);
    }
    elsif ( -d $data_value )
    {
      my $vendor;
      my $product;
      my @dummy = split("/", $data_value);
      $vendor = $dummy[ ($#dummy - 1) ];
      $product = $dummy[$#dummy];
      my $html_path = $main::HTML_DIR . "/" . $vendor . "/" .
                      $product . "/" . "index.html";

      if ( -f $html_path )
      {
        my $description = ::extractTable($html_path);

        my $status_page = "
<html>
<head>
  <title>$vendor $product</title>
</head>
<body>
<h3>$vendor $product</h3>
<p>
$description
</p>
</body>
</html>
";
        if ($description)
	{
          $this->{NOTEBOOK}->DeleteAllPages();

          $this->{TEXTCTRL4} = Wx::HtmlWindow->new($this->{NOTEBOOK}, -1);
          $this->{NOTEBOOK}->AddPage( $this->{TEXTCTRL4}, "Status",1 );
          $this->{TEXTCTRL4}->SetPage($status_page);
	}

      }
    }
    else
    {
      my $title = "Administration";
      my $description = "An opensource monitoring engine.";
      if ( $data_value eq "monitors" )
      {
        $title = "Monitors";
        $description = ::extractTable("$main::HTML_DIR/index.html");
      }
      elsif ( $data_value eq "ftmon" )
      {
        $title = "ftmon";
        $description = "Configuration specific to FTMON itself";
      }
      elsif ( $data_value eq "event_manager" )
      {
        $title = "Event Manager";
        $description = "Configure Event Manager.";
      }
      $this->{NOTEBOOK}->DeleteAllPages();
      
      # <TABLE BORDER=1 COLS=5 WIDTH="900" CELLPADDING="0" CELLSPACING="3"
    my $status_page = "
<html>
<head>
  <title>$title</title>
</head>
<body>
<h3>$title</h3>
<p>
$description
</p>
</body>
</html>
";
    $this->{TEXTCTRL4} = Wx::HtmlWindow->new($this->{NOTEBOOK}, -1);
    $this->{NOTEBOOK}->AddPage( $this->{TEXTCTRL4}, "Help",1 );
    $this->{TEXTCTRL4}->SetPage($status_page);
    }
  }
  # Wx::LogMessage( 'Data: %s', $this->{TREECTRL}->GetPlData( $item ) );


}

sub quotedSplit
{
  my ( $l_line, $l_s ) = @_;
  my ( $l_sep_last );
  my ( $l_field );
  my ( @l_fields ) = ();

  $l_s = "," if ( ! defined( $l_s )  );

  # $U::DEBUG && 
  # CC::trace( S, "CC::quotedSplit(\n  \'$l_line\',\n  ... )" );

  #
  # The following is complicated by the need to support both perl4 & perl5
  #
  while ( $l_line =~ 
       
    /\s*[\'\"]([^\'\"\\]*(\\.[^\'\"\\]*)*)[\'\"]\s*($l_s)?|([^${l_s}]+)($l_s)?|()($l_s)/g )
  {
    if ( $1 )
    {
      $l_field = "$1";
      $l_field =~ s/^\s*//o;
      $l_field =~ s/\s*$//o;
      push( @l_fields, $l_field );
    }
    else
    {
      $l_field = ( defined( $4) ) ? "$4" : "";
      
      $l_field =~ s/^\s*//o;
      $l_field =~ s/\s*$//o;
      push( @l_fields, $l_field );
    }
  }

  $l_sep_last = length($l_s);
  if ( substr($l_line,  -$l_sep_last, $l_sep_last ) eq "$l_s" )
  {
    push(@l_fields, "" );
  }

  # $U::DEBUG && CC::trace( B, "CC::quotedSplit ( @l_fields )" );
  # $U::DEBUG && CC::trace( R, "( @l_fields ) - Matched" );
  return( @l_fields );
}

sub CheckFile
{
  my( $this, $file ) = @_;
  $FT::VENDOR = "";
  $FT::PRODUCT = "";
  $FT::MONITOR::NAME = "";
  $FT::PACKAGE = "";
  $FT::MONITOR::DESC = "";
  $FT::MONITOR::VER = "";
  $FT::MONITOR::SCHED = "";

  my @dirs = split('/', $file);
  my $mon_dir = $file;
  my $file_name = $dirs[$#dirs];
  $mon_dir =~ s/$file_name//;
  chdir($mon_dir);

  #eval 
  #{
  #  package MONITOR;
  #  do $file;
  #  package MyFrame;
  #};
  #
  #if ( $@ )
  #{
  #  Wx::LogMessage( $@ );
  #}


  #eval "package $FT::PACKAGE; do \"$file\";";
  eval "do \"$file\";";
  if ( $@ )
  {
    Wx::LogMessage( $@ );
  }

  ( $FT::VENDOR,
    $FT::PRODUCT,
    $FT::MONITOR::NAME ) = split("::", $FT::PACKAGE);

  Wx::LogMessage( $FT::PACKAGE );
}

sub LoadImplFile
{
  my( $this, $file, $variables ) = @_;


  my $impl_file = $file;
  $impl_file =~ s/\/([\w_]*)\.cfg/\/impl\/$1\.cfg/;

  print "Load $impl_file \n\n";
  my $source = "";
  my $variable;
  my $variable_type;
  my $value;
  my $comment = "";

  open(FILE, "< $impl_file") || die "Could not open '$impl_file': $1";
  $comment = "$impl_file : \n";
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

      next if ( $variable !~ /(_P|_V|_A|_M)$/ &&
                $variable !~ /^FT/ );

      next if ( $variable =~ /FT::PRODUCT$/ );
      next if ( $variable =~ /FT::VENDOR/ );
      next if ( $variable =~ /FT::MONITOR::/ );
      next if ( $variable =~ /FT::PRODUCT::SUMMARY/ );
      next if ( $variable =~ /FT::PRODUCT::DESC/ );
      next if ( $variable =~ /FT::INTERP/ );

      if ( $value =~ /^\'.*\'$/ )
      {
        $variable_type = $VARIABLE_SINGLE_QUOTES;
	$value = $1;
      }
      elsif ( $value =~ /^\"(.*)\"$/ )
      {
        $variable_type = $VARIABLE_DOUBLE_QUOTES;
	$value = $1;
      }
      elsif ( $value =~ /^[\d+]*\.[\d]*$/ )
      {
        $variable_type = $VARIABLE_FLOAT;
      }
      elsif ( $variable =~ /::DEBUG/ || $variable =~ /::IS_/ )
      {
        $variable_type = $VARIABLE_BOOL;
      }
      elsif ( $value =~ /^[\d+]+$/ )
      {
        $variable_type = $VARIABLE_ARRAY;
      }
      elsif ( $value =~ /^\[.*\]$/ )
      {
        $variable_type = $VARIABLE_ARRAY;
      }

      $comment = $comment . " DEFAULT_VALUE: $value, TYPE: $variable_type ";
      $variables->{$variable} = [-1, $value, $comment, $variable_type, $value];
      $comment = "";
    }
  }
  close(FILE);
}

sub LoadFile
{
  my( $this, $file, $variables ) = @_;


  my $source = "";
  my $row = 0;
  my $variable;
  my $variable_type;
  my $value;
  my @thresholds;
  my @fields;
  my $comment = "";
  my $num_thresholds = 0;

  open(FILE, "< $file") || die "Could not open '$file': $1";
  $comment = "$file : \n";
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
      Wx::LogMessage( "%s", "$comment" );
      $FT::FILE::DESC = $comment;
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

      next if ( $variable =~ /FT::PRODUCT$/ );
      next if ( $variable =~ /FT::VENDOR/ );
      next if ( $variable =~ /FT::MONITOR::/ );
      next if ( $variable =~ /FT::PRODUCT::SUMMARY/ );
      next if ( $variable =~ /FT::PRODUCT::DESC/ );
      next if ( $variable =~ /FT::INTERP/ );

      if ( $value =~ /^\'.*\'$/ )
      {
        $variable_type = $VARIABLE_SINGLE_QUOTES;
	$value = $1;
      }
      elsif ( $value =~ /^\"(.*)\"$/ )
      {
        $variable_type = $VARIABLE_DOUBLE_QUOTES;
	$value = $1;
      }
      elsif ( $value =~ /^[\d+]*\.[\d]*$/ )
      {
        $variable_type = $VARIABLE_FLOAT;
      }
      elsif ( $variable =~ /::DEBUG/ || $variable =~ /::IS_/ )
      {
        $variable_type = $VARIABLE_BOOL;
      }
      elsif ( $value =~ /^[\d+]+$/ )
      {
        $variable_type = $VARIABLE_INTEGER;
      }
      elsif ( $value =~ /^\[.*\]$/ )
      {
        $variable_type = $VARIABLE_ARRAY;
      }

      $comment = ( $comment eq "" && defined $variables->{$variable} ) 
                       ?  $variables->{$variable}->[2] 
		       : $comment;
      my $default_value = "";
      $default_value = $variables->{$variable}->[1] 
              if ( defined $variables->{$variable} );

      $variables->{$variable} = 
           [$row, $value, $comment, $variable_type, $default_value];
      $row++;
      $comment = "";
    }
    elsif ( /^\[.*\],$/)
    {
      s/^\[//;
      s/\],$//;
      @fields = quotedSplit($_);
      $comment = "";

      if ( @fields > 6 )
      {
	my $old_field;
	my $array = 0;
	my $new_field;
        my @old_fields = @fields;
	@fields = ();
	foreach $old_field (@old_fields)
	{
	  if ( $old_field =~ /^\[/ )
	  {
	    $array = 1;
	    $new_field = $old_field;
	  }
	  elsif ( $array )
	  {
	     if ( $old_field =~ /\]$/ )
	     {
	       $array = 0;
	       $new_field = $new_field . $old_field;
	       push(@fields, $new_field);
	     }
	     else
	     {
	       $old_field = "'$old_field'" 
	          if ( $old_field !~ /^\$/ );
	       $new_field = $new_field . ", " . $old_field;
	     }
	  }
	  else
	  {
	    push(@fields, $old_field);
	  }
	}
      }


      push(@thresholds, [ @fields ] );
      $num_thresholds++;
    }
  }
  close(FILE);

  my $num_rows = $row;
  my $cfg_page = -1;
  my $var_page = -1;
  my $new = 0;

    $this->{TEXTCTRL2} = ConfigGrid->new(
	               $this->{NOTEBOOK}, -1, wxDefaultPosition, wxDefaultSize);
    $this->{NOTEBOOK}->AddPage( $this->{TEXTCTRL2}, "Variables", 1 );
    $var_page = $this->{NOTEBOOK}->GetSelection();
    $this->{TEXTCTRL2}->loadGrid($file, $variables);
    $new++;

  if ( $num_thresholds )
  {
    $this->{TEXTCTRL3} = ThresholdGrid->new(
	               $this->{NOTEBOOK}, -1, wxDefaultPosition, wxDefaultSize);
    $this->{NOTEBOOK}->AddPage( $this->{TEXTCTRL3}, "Thresholds",1 );
    $cfg_page = $this->{NOTEBOOK}->GetSelection();
    $this->{TEXTCTRL3}->loadGrid($file, \@thresholds);
    $new++;
  }

      Wx::LogMessage( "%s", "||| $FT::MONITOR::NAME" );
  if ( $FT::MONITOR::NAME )
  {
    my $description = ::extractTable(
      $main::HTML_DIR . "/" . $FT::VENDOR . "/" .
      $FT::PRODUCT . "/" . $FT::MONITOR::NAME . "_status.html");

    my $status_page = "
<html>
<head>
  <title>$FT::VENDOR $FT::PRODUCT $FT::MONITOR::NAME</title>
</head>
<body>
<h3>$FT::VENDOR $FT::PRODUCT $FT::MONITOR::NAME</h3>
<p>
$description
</p>
</body>
</html>
";
    if ( $description )
    {
      $this->{TEXTCTRL4} = Wx::HtmlWindow->new($this->{NOTEBOOK}, -1);
      $this->{NOTEBOOK}->AddPage( $this->{TEXTCTRL4}, "Status",1 );
      $this->{TEXTCTRL4}->SetPage($status_page);
      $new++;
    }


    my $description = ::extractTable(
      $main::HTML_DIR . "/" . $FT::VENDOR . "/" . 
      $FT::PRODUCT . "/" . $FT::MONITOR::NAME . "_events.html");

    my $status_page = "
<html>
<head>
  <title>$FT::VENDOR $FT::PRODUCT $FT::MONITOR::NAME</title>
</head>
<body>
<h3>$FT::VENDOR $FT::PRODUCT $FT::MONITOR::NAME</h3>
<p>
$description
</p>
</body>
</html>
";
    if ( $description )
    {
      $this->{TEXTCTRL5} = Wx::HtmlWindow->new($this->{NOTEBOOK}, -1);
      $this->{NOTEBOOK}->AddPage( $this->{TEXTCTRL5}, "Events",1 );
      $this->{TEXTCTRL5}->SetPage($status_page);
      $new++;
    }



    $status_page = "
<html>
<head>
  <title>$FT::VENDOR $FT::PRODUCT $FT::MONITOR::NAME</title>
</head>
<body>
<h3>$FT::VENDOR $FT::PRODUCT $FT::MONITOR::NAME</h3>
<p>
$FT::PRODUCT::SUMMARY
$FT::PRODUCT::DESC
The local contact for this application is $FT::PRODUCT::CONTACT
</p>
<p>
$FT::MONITOR::DESC <br>
The version of this monitor is $FT::MONITOR::VER.<br>
The monitoring schedule is $FT::MONITOR::SCHED.
</p>
<p>
The current status of the monitor can be viewed by selecting the Config->Html menu item ($FT::VENDOR/$FT::PRODUCT/$FT::MONITOR::NAME.html).
</p>
</body>
</html>
";
    if ( $description )
    {
      $this->{TEXTCTRL6} = Wx::HtmlWindow->new($this->{NOTEBOOK}, -1);
      $this->{NOTEBOOK}->AddPage( $this->{TEXTCTRL6}, "Help",1 );
      $this->{TEXTCTRL6}->SetPage($status_page);
      $new++;
    }

    my $graph;


    my $graph_dir = $main::HTML_DIR .  "/" .
                    $FT::VENDOR . "/" .
		    $FT::PRODUCT;


    my $graph_prefix = "graph_" . $FT::VENDOR . "_" .
                         $FT::PRODUCT . "_" . 
			 $FT::MONITOR::NAME . "_";

    my $graph_path;
    my $today_html_src = "";
    my $weekly_html_src = "";
    my $monthly_html_src = "";
    my $rollover_html_src = "";
    my @rollover_html_files = ();
    opendir(GRAPHS, $graph_dir) || die  "Could not open '$graph_dir'";
    foreach $graph ( readdir(GRAPHS) )
    {
      chomp;

      next if ( $graph !~ /^${graph_prefix}/ );
      next if ( $graph !~ /\.gif$/ );
      $graph_path = $graph_dir . "/".  $graph;
      
      if ( $graph =~ /_rollover/ )
      {
        push(@rollover_html_files, $graph_path);
      }
      elsif ( $graph =~ /_monthly_avg/ )
      {
        $monthly_html_src = 
           $monthly_html_src . 
           "<IMG SRC=\"$graph_path\" BORDER=\"0\"><br>";
      }
      elsif ( $graph =~ /_weekly_avg/ )
      {
        $weekly_html_src = 
           $weekly_html_src . 
           "<IMG SRC=\"$graph_path\" BORDER=\"0\"><br>";
      }
      else
      {
        $today_html_src = 
           $today_html_src . 
           "<IMG SRC=\"$graph_path\" BORDER=\"0\"><br>";
      }
    }
    closedir(GRAPHS);

    my @sorted_rollover_files = sort { -M $a > -M $b } @rollover_html_files;
    foreach $graph_path ( @sorted_rollover_files )
    {
        $rollover_html_src = 
           $rollover_html_src . 
           "<IMG SRC=\"$graph_path\" BORDER=\"0\"><br>";
    }

    if ( $today_html_src )
    {
      $today_html_src = "
<html>
<head>
  <title>$file</title>
</head>
<body>
<p>
</p>
" .
      $today_html_src .
"
</p>
</body>
</html>
";

      $this->{TEXTCTRL7} = Wx::HtmlWindow->new($this->{NOTEBOOK}, -1);
      $this->{NOTEBOOK}->AddPage( $this->{TEXTCTRL7}, "Today",1 );
      $this->{TEXTCTRL7}->SetPage($today_html_src);
      $new++;
    }

    if ( $rollover_html_src )
    {
      $rollover_html_src = "
<html>
<head>
  <title>$file</title>
</head>
<body>
<p>
</p>
" .
      $rollover_html_src .
"
</p>
</body>
</html>
";

      $this->{TEXTCTRL8} = Wx::HtmlWindow->new($this->{NOTEBOOK}, -1);
      $this->{NOTEBOOK}->AddPage( $this->{TEXTCTRL8}, "Daily",1 );
      $this->{TEXTCTRL8}->SetPage($today_html_src);
      $new++;
    }

    if ( $weekly_html_src )
    {
      $weekly_html_src = "
<html>
<head>
  <title>$file</title>
</head>
<body>
<p>
</p>
" .
      $weekly_html_src .
"
</p>
</body>
</html>
";

      $this->{TEXTCTRL9} = Wx::HtmlWindow->new($this->{NOTEBOOK}, -1);
      $this->{NOTEBOOK}->AddPage( $this->{TEXTCTRL9}, "Weekly",1 );
      $this->{TEXTCTRL9}->SetPage($weekly_html_src);
      $new++;
    }

    if ( $monthly_html_src )
    {
      $monthly_html_src = "
<html>
<head>
  <title>$file</title>
</head>
<body>
<p>
</p>
" .
      $monthly_html_src .
"
</p>
</body>
</html>
";

      $this->{TEXTCTRL9} = Wx::HtmlWindow->new($this->{NOTEBOOK}, -1);
      $this->{NOTEBOOK}->AddPage( $this->{TEXTCTRL9}, "Monthly",1 );
      $this->{TEXTCTRL9}->SetPage($monthly_html_src);
      $new++;
    }

  }
  elsif ( $FT::FILE::DESC )
  {
    my $status_page = "
<html>
<head>
  <title>$file</title>
</head>
<body>
<h3>$file</h3>
<p>
$FT::FILE::DESC
</p>
</body>
</html>
";
    $this->{TEXTCTRL11} = Wx::HtmlWindow->new($this->{NOTEBOOK}, -1);
    $this->{NOTEBOOK}->AddPage( $this->{TEXTCTRL11}, "Help",1 );
    $this->{TEXTCTRL11}->SetPage($status_page);
    $new++;
  }


  my $old = $this->{NOTEBOOK}->GetPageCount() - $new;

  my $i = 0;
  for ($i = 0; $i < $old; $i++)
  {
    print "remove $i ($old) \n";
    $this->{NOTEBOOK}->RemovePage(0);
  }



  $this->{NOTEBOOK}->Refresh();
}



sub DESTROY {
  my $this = shift;

  Wx::Log::SetActiveTarget( $this->{OLDLOG} )->Destroy;
}

sub OnQuit {
  my( $this, $event ) = @_;

  $this->Close( 1 );
}

sub LoadCfg {
  my( $this, $event ) = @_;

  my $winnt_cfg = $ENV{'SystemRoot'} . "/FTMON/";
  $winnt_cfg =~ s/\\/\//g;

  my $unix_cfg = "/etc";
  my $ftmon_cfg = $winnt_cfg;

  
  my $config_file = "";
  $config_file = Wx::FileSelector(
	            'Load ftmon.cfg', $ftmon_cfg, "ftmon.cfg", ".cfg");
  print "|", $config_file, "\n";
  if ( $config_file )
  {
    package main;
    do "$config_file";
    package MyFrame;
    $this->{TREECTRL}->UnselectAll;
    $this->{TREECTRL}->DeleteAllItems();
    $this->{TREECTRL}->PopulateTree( 3, 2 );
  }
}

sub OnAbout {
  my( $this, $event ) = @_;

  use Wx qw(wxOK);

  Wx::MessageBox( "Wx::TreeCtrl sample", "About sample", wxOK, $this );
}

sub OnSave {
  my( $this, $event ) = @_;

  Wx::MessageBox( "Saving Config file" );
  $this->{TEXTCTRL2}->SaveConfig();
}

sub OnHtml {
  my( $this, $event ) = @_;

  Wx::MessageBox( "Opening HTML file" );
  my $html = $this->{TREECTRL}->{SELECTED_CONFIG_FILE};
  $html =~ s/\.cfg/\.html/;
  $html =~ s/cfg/html/g;
  if ( -e $html )
  {
    my $html_nt = $html;
    $html_nt =~ s/\//\\/g;
    #system("start", $ENV{'SystemRoot'} . "\\explorer",  $html_nt);
    # Replace with explorer, netscape etc.
    system("start", "explorer",  $html_nt);

    #my $html_window = HtmlWindowWin->new($this->{NOTEBOOK}, -1);
    #$this->{NOTEBOOK}->AddPage( $html_window, "HTML", 1 );
    #$html_window->LoadPage($html);
  }
}


sub OnSelect {
  my( $this, $event ) = @_;

  $this->{TREECTRL}->SelectItem( $this->{TREECTRL}->GetSelection );
}

sub OnDeselect {
  my( $this, $event ) = @_;

  $this->{TREECTRL}->Unselect;
}

sub OnDeselectAll {
  my( $this, $event ) = @_;

  $this->{TREECTRL}->UnselectAll;
}

package MyTreeCtrl;

use strict;
use vars qw(@ISA);

@ISA = qw(Wx::TreeCtrl);

sub ResizeTo {
  my( $image, $size ) = @_;

  if( $image->GetWidth != $size || $image->GetHeight != $size ) {
    return Wx::Image->new( $image )->Rescale( $size, $size )
      ->ConvertToBitmap();
  }

  return $image;
}

sub new {
  my $class = shift;
  my $this = $class->SUPER::new( @_ );

  #$this->{IMAGELIST} = Wx::ImageList->new( 16, 16, 1 );
  #$this->{IMAGELIST}->Add( Wx::GetWxPerlIcon( 1 ) );
  #$this->{IMAGELIST}->Add
  #  ( ResizeTo( Wx::wxTheApp()->GetStdIcon( Wx::wxICON_EXCLAMATION() ), 16 ) );

  #$this->SetImageList( $this->{IMAGELIST} );
  $this->PopulateTree( 3, 2 );

  $this;
}

sub PopulateTree {
  my( $this, $childs, $depth ) = @_;

  my $root = $this->AddRoot(
                'FTMON Root', -1, -1, Wx::TreeItemData->new( 'Data' ) );

  $this->PopulateRecursively( $root, $childs, $depth );
}

sub PopulateRecursively {
  my( $this, $parent, $childs, $depth ) = @_;
  my( $text, $item, $vendor_item, $product_item, $monitor_item );

  use Wx qw(wxITALIC_FONT wxRED wxBLUE wxGREEN);

  if ( ! opendir(VENDOR, $main::CFG_DIR) )
  {
    return;
  }
  my $monitors;
  my $ftmon;
  my $vendor;
  my $vendor_dir;
  my $product_dir;
  my $product;
  my $monitor_dir;
  my $monitor;
  my $item;
  $monitors = $this->AppendItem( $parent, "monitors", 0, 1,
                               Wx::TreeItemData->new( "monitors" ) );
  $this->EnsureVisible($monitors);

  $ftmon = $this->AppendItem( $parent, "ftmon", 0, 1,
                               Wx::TreeItemData->new( "ftmon" ) );
  $this->EnsureVisible($ftmon);

  foreach $vendor ( readdir(VENDOR) )
  {
    chomp $vendor;
    $item = $vendor;
    $item =~ s/\.cfg$//;
    $vendor_dir = $main::CFG_DIR . "/" . $vendor;
    next if ( $vendor eq '.' );
    next if ( $vendor eq '..' );


    if ( $vendor =~ /\.cfg$/ )
    {
      next if ( $vendor =~ /^event_manager/ );

      $vendor_item = $this->AppendItem( $ftmon, $item, 0, 1,
                               Wx::TreeItemData->new( $vendor_dir ) );
      $this->SetItemTextColour( $vendor_item, wxGREEN );

      if ( $product =~ /^common\.cfg/ )
      {
        $this->SetItemFont( $vendor_item, wxITALIC_FONT );
        $this->SetItemTextColour( $vendor_item, wxBLUE );
      }

      next;
    }

    next if ( ! -d $vendor_dir );
    if ( $vendor =~ /event_manager/ )
    {
      $vendor_item = $this->AppendItem( $parent, $item, 0, 1,
                               Wx::TreeItemData->new( $vendor_dir ) );
      $this->EnsureVisible($vendor_item);
    }
    else
    {
      $vendor_item = $this->AppendItem( $monitors, $item, 0, 1,
                               Wx::TreeItemData->new( $vendor_dir ) );
    }
    opendir(PRODUCT, $vendor_dir) || die "Could not open '$vendor_dir'";
    foreach $product ( readdir(PRODUCT) )
    {
      chomp $product;
      $item = $product;
      $item =~ s/\.cfg$//;
      $product_dir = $vendor_dir . "/" . $product;
      next if ( $product eq '.' );
      next if ( $product eq '..' );
      if ( $product =~ /\.cfg$/ )
      {
        $product_item = $this->AppendItem( $vendor_item, $item, 0, 1,
	                       Wx::TreeItemData->new( $product_dir ) );
        $this->SetItemTextColour( $product_item, wxGREEN );
	if ( $product =~ /^common\.cfg/ )
	{
          $this->SetItemFont( $product_item, wxITALIC_FONT );
          $this->SetItemTextColour( $product_item, wxBLUE )
	}
	next;
      }
      next if ( ! -d $product_dir );
      $product_item = $this->AppendItem( $vendor_item, $product, 0, 1,
                               Wx::TreeItemData->new( $product_dir ) );
      opendir(MONITOR, $product_dir) || die "Could not open '$product_dir'";
      foreach $monitor ( readdir(MONITOR) )
      {
        chomp $monitor;
        $item = $monitor;
        $item =~ s/\.cfg$//;
        $monitor_dir = $product_dir . "/" . $monitor;
        next if ( $monitor eq '.' );
        next if ( $monitor eq '..' );
        if ( $monitor =~ /\.cfg$/ )
        {
	  $monitor_item = $this->AppendItem( $product_item, $item, 0, 1,
	                       Wx::TreeItemData->new( $monitor_dir ) );
          $this->SetItemTextColour( $monitor_item, wxGREEN );
          $this->SetItemTextColour( $monitor_item, wxBLUE )
	      if ( $monitor =~ /^common\.cfg/ );
	  next;
        }
      }
      close(MONITOR);
      
    }
    close(PRODUCT);
  }
  close(VENDOR);
  return;



  foreach my $i ( 1 .. $childs ) {
    my $text = ( $depth > 0 ) ? "Node $i/$childs" : "Leaf $i/$childs";

    $item = $this->AppendItem( $parent, $text, 0, 1,
                               Wx::TreeItemData->new( $text ) );
    $this->SetItemFont( $item, wxITALIC_FONT ) if $depth == 0;
    $this->SetItemBackgroundColour( $item, wxBLUE ) if $depth == 1;
    $this->SetItemTextColour( $item, wxGREEN ) if $depth == 2;

    if( $i == 2 ) {
      my $t = Wx::TreeItemData->new; $t->SetData( "Foo $i" );
      $this->SetItemData( $item, $t );
    }
    $this->SetPlData( $item, "Bar $i" )
      if $i == 3;
    #FIXME// see bugs.txt
#    $this->GetItemData( $item )->SetData( "A" )
#      if $i == 4;

#$this->PopulateRecursively( $item, $childs + 1, $depth - 1 )
#      if $depth >= 1;
  }
}

sub DoSortChildren {
  my( $this, $item, $ascending ) = @_;

  $this->{REVERSESORT} = !$ascending;
  $this->SortChildren( $item );
}

sub OnCompareItems {
  my( $this, $item1, $item2 ) = @_;

  if( $this->{REVERSESORT} ) {
    return $this->SUPER::OnCompareItems( $item2, $item1 );
  } else {
    return $this->SUPER::OnCompareItems( $item1, $item2 );
  }
}

sub GetItemsRecursively {
  my( $this, $parent, $cookie ) = @_;
  my $id;

  if( $cookie <= 0 ) { ( $id, $cookie ) = $this->GetFirstChild( $parent ) }
  else { ( $id, $cookie ) = $this->GetNextChild( $parent, $cookie ) }

  return unless $id->IsOk;

  Wx::LogMessage( "%s", $this->GetItemText( $id ) );

  if( $this->ItemHasChildren( $id ) ) {
    $this->GetItemsRecursively( $id, -1 );
  }

  $this->GetItemsRecursively( $id, $cookie );
}

package MyApp;

use strict;
use vars qw(@ISA);

@ISA = qw(Wx::App);

use Wx qw(:splashscreen wxBITMAP_TYPE_JPEG wxBITMAP_TYPE_ICO);

sub OnInit {
  my $this = shift;

  my $frame = MyFrame->new( "Administration", 10, 10, 1000, 450 );
  $frame->Show( 1 );

  my $bitmap_file = $main::BASE_DIR . "/ftmon_banner2.jpg";
  my $bitmap = Wx::Bitmap->new( $bitmap_file, wxBITMAP_TYPE_JPEG );
  my $splash = Wx::SplashScreen->new( $bitmap,
                           wxSPLASH_CENTRE_ON_SCREEN|wxSPLASH_TIMEOUT,
                           2000, $frame, -1 );

  #$this->SetTopWindow( $frame );
  #$this->SetTopWindow( $splash );

  # REVISIT:
  # Move this to ftmon.pl and only do on NT
  #
  #my $task_bar = Wx::TaskBarIcon->new;
  #my $icon = Wx::Icon->new("f:/temp/ftmon2_small.bmp",
  #	  wxBITMAP_TYPE_ICO, -1, -1 );
  #$task_bar->SetIcon($icon);

  1;
}


package ConfigGrid;

use strict;
use vars qw(@ISA); @ISA = qw(Wx::Panel);

use Wx qw(:sizer);

use Wx::Event qw(EVT_GRID_CELL_LEFT_CLICK EVT_GRID_CELL_RIGHT_CLICK
    EVT_GRID_CELL_LEFT_DCLICK EVT_GRID_CELL_RIGHT_DCLICK
    EVT_GRID_LABEL_LEFT_CLICK EVT_GRID_LABEL_RIGHT_CLICK
    EVT_GRID_LABEL_LEFT_DCLICK EVT_GRID_LABEL_RIGHT_DCLICK
    EVT_GRID_ROW_SIZE EVT_GRID_COL_SIZE EVT_GRID_RANGE_SELECT
    EVT_GRID_CELL_CHANGE EVT_GRID_SELECT_CELL EVT_BUTTON );

use Wx qw(wxRED wxBLUE wxGREEN);

sub new {
  my $class = shift;
  my $this = $class->SUPER::new( $_[0], -1 );

  $this->{GRID} = Wx::Grid->new($this, -1);


  my $top_s = Wx::BoxSizer->new(wxVERTICAL);
  my $but_s = Wx::BoxSizer->new(wxHORIZONTAL);

  my $insert_above = Wx::Button->new($this, -1, 'InsertAbove');
  my $insert_below = Wx::Button->new($this, -1, 'InsertBelow');
  my $delete = Wx::Button->new($this, -1, 'Delete');

  $but_s->Add($insert_above);
  $but_s->Add($insert_below);
  $but_s->Add($delete);

  $top_s->Add($this->{GRID}, 1, wxGROW|wxALL, 5);
  $top_s->Add($but_s, 0, wxALL, 5);

  $this->SetSizer($top_s);
  $this->SetAutoLayout(1);

  $this->{GRID}->CreateGrid(1,2);
  $this->{GRID}->SetColLabelValue(0, "Variable");
  $this->{GRID}->SetColLabelValue(1, "Value");

  $this->{CURRENT_ROW} = -1;


  EVT_GRID_CELL_LEFT_CLICK( $this, c_log_skip( "Cell left click" ) );
  EVT_GRID_CELL_RIGHT_CLICK( $this, \&CellHelp );
  EVT_GRID_CELL_LEFT_DCLICK( $this, c_log_skip( "Cell left double click" ) );
  EVT_GRID_CELL_RIGHT_DCLICK( $this, c_log_skip( "Cell right double click" ) );
  EVT_GRID_LABEL_LEFT_CLICK( $this, c_log_skip( "Label left click" ) );
  EVT_GRID_LABEL_RIGHT_CLICK( $this, c_log_skip( "Label right click" ) );
  EVT_GRID_LABEL_LEFT_DCLICK( $this, c_log_skip( "Label left double click" ) );
  EVT_GRID_LABEL_RIGHT_DCLICK( $this, c_log_skip( "Label right double click" ) );

  EVT_GRID_ROW_SIZE( $this, sub {
                       Wx::LogMessage( "%s %s", "Row size", GS2S( $_[1] ) );
                       $_[1]->Skip;
                     } );
  EVT_GRID_COL_SIZE( $this, sub {
                       Wx::LogMessage( "%s %s", "Col size", GS2S( $_[1] ) );
                       $_[1]->Skip;
                     } );

  EVT_GRID_RANGE_SELECT( $this, sub {
                           Wx::LogMessage( "Range %sselect (%d, %d, %d, %d)",
                                           ( $_[1]->Selecting ? '' : 'de' ),
                                           $_[1]->GetLeftCol, $_[1]->GetTopRow,
                                           $_[1]->GetRightCol,
                                           $_[1]->GetBottomRow );
                           $_[1]->Skip;
                         } );
  EVT_GRID_CELL_CHANGE( $this, \&CellChange);
  EVT_GRID_SELECT_CELL( $this,  sub {
                       my( $col, $row ) = ( $_[1]->GetCol, $_[1]->GetRow );
                       $this->{CURRENT_ROW} = $row;
                       Wx::LogMessage( "select %s %s", $row, $col );
                       $_[1]->Skip;
                         } );

  EVT_BUTTON( $this, $insert_above, \&InsertRowAbove);
  EVT_BUTTON( $this, $insert_below, \&InsertRowBelow);
  EVT_BUTTON( $this, $delete, \&DeleteRow);

  return $this;
}

sub CellChange
{
  my $this = shift;
  my $event = shift;
  use Wx qw(wxOK);

  my( $col, $row ) = ( $event->GetCol, $event->GetRow );
  my $variable = $this->{GRID}->GetCellValueXY($row, 0);
  my $value = $this->{GRID}->GetCellValueXY($row, 1);
  $variable =~ s/^\$//;

  return if ( $col != 0 );

  if ( defined $this->{VARIABLES}->{$variable} && 
         defined $this->{VARIABLES}->{$variable}->[4] )
  {
    $value = $this->{VARIABLES}->{$variable}->[4];
    $this->{GRID}->SetCellValue($row, 1, $value);
  }
}

sub CellHelp
{
  my $this = shift;
  my $event = shift;
  use Wx qw(wxOK);

  my( $col, $row ) = ( $event->GetCol, $event->GetRow );
  my $variable = $this->{GRID}->GetCellValueXY($row, $col);
  $variable =~ s/^\$//;
  my $value = "undefined";
  my $variable_short = $variable;
  $variable_short =~ s/\{.*\}//;
  $variable_short =~ s/\[.*\]//;

  my $comments = "";
  $comments = $this->{COMMENTS}->{$variable_short}
      if ( exists $this->{COMMENTS}->{$variable_short} );

  if ( $variable =~ /^[A-Z_]/ )
  {
    if ( $variable =~ /::/ )
    {
      eval "\$value = \$$variable";
    }
    else
    {
      eval "\$value = \$${FT::PACKAGE}::$variable";
    }
    if ( ref($value) eq "ARRAY" )
    {
      my $new_value = "[ ";
      foreach (@$value)
      {
        $new_value = $new_value . $_ . ", ";
      }
      $new_value =~ s/, $/\]/;
      $value = $new_value;
    }
    Wx::MessageBox( 
	 "( $value ): " . $comments,
         $variable, wxOK, $this );
  }
}

sub InsertRowAbove
{
  my $this = shift;
  my $event = shift;

  if ( defined $this->{CURRENT_ROW} )
  {
    my $row = $this->{CURRENT_ROW};
    $this->{GRID}->InsertRows($row);
    Wx::LogMessage( "insert row");
  }
}

sub InsertRowBelow
{
  my $this = shift;
  my $event = shift;

  if ( defined $this->{CURRENT_ROW} )
  {
    my $row = $this->{CURRENT_ROW};
    $this->{GRID}->InsertRows($row + 1);
    my @selections;
    my $key;
    my $value;
    
    #    [$row, $value, $comment, $variable_type, $default_value];


    while ( ($key, $value) = each %{$this->{VARIABLES}} )
    {
      push(@selections, $key) if ( $key !~ /_V$/ );
    }

    $this->{GRID}->SetCellEditor( 
	     $row + 1 , 0, Wx::GridCellChoiceEditor->new([@selections], 1));
    $this->{GRID}->SetCellValue($row + 1, 1, undef);
    Wx::LogMessage( "insert row");
  }

}

sub DeleteRow
{
  my $this = shift;
  my $event = shift;

  my $num_rows = $this->{GRID}->GetNumberRows();
  if ( $num_rows && $this->{CURRENT_ROW} != -1 )
  {
    my $row = $this->{CURRENT_ROW};
    $this->{GRID}->DeleteRows($row);
    $this->{CURRENT_ROW} = -1;
    Wx::LogMessage( "delete row");
  }
}

sub SaveConfig
{
  my $this = shift;

  my $file = $this->{CONFIG_FILE};
  $file =~ s/\.cfg/\.tmp1/;


  open( CFG, "> $file" ) || die "Could not open `$file`: $!";

  my $col;
  my $row;
  my $line;
  my $num_cols = $this->{GRID}->GetNumberCols();
  my $num_rows = $this->{GRID}->GetNumberRows();
  Wx::LogMessage( "Saving Config to $file - cols = $num_cols, rows = $num_rows");
  for ( $row = 0; $row < $num_rows; $row++ )
  {
    $line = "\$";
    for ( $col = 0; $col < $num_cols; $col++ )
    {
      $line = $line . $this->{GRID}->GetCellValueXY($row,$col);
      $line = $line . " = " if ( $col == 0 );
    }
    if ( $line =~ /\$ =/ )
    {
      print CFG "\n";
    }
    else
    {
      print CFG $line, ";\n";
    }
  }
  close(CFG);

  Wx::LogMessage( "Saved Config to $file");
}

sub loadGrid
{
  my $this = shift;
  my $file = shift;
  my $variables = shift;

  my $key;
  my $value;

  $this->{VARIABLES} = $variables;


  my %variables = %{$variables};
  while ( ($key, $value) = each %variables )
  {
    delete $variables{$key} if ( $value->[0] == -1 );
  }

  $this->{CONFIG_FILE} = $file;

  my @rows = %variables;
  my $num_rows = @rows / 2;
  return if ( $num_rows == 0 );

  $this->{GRID}->DeleteCols(0, 2);
  $this->{GRID}->DeleteRows(0, 500);

  $this->{GRID}->InsertRows(0, $num_rows);
  $this->{GRID}->InsertCols(0, 2);

  $this->{GRID}->SetColSize(0, 300);
  $this->{GRID}->SetColSize(1, 300);

  #my $attr = Wx::GridCellAttr->new;
  #$attr->SetReadOnly();
  #$this->{GRID}->SetColAttr(0, $attr);

  my $row = 0;
  my $variable;
  my $variable_type;
  my $value;
  my $item;
  my @variables = 
       sort { $variables{$a}->[0] <=> $variables{$b}->[0] } 
           keys %variables;
  foreach $variable (@variables)
  {
      my $variable_short = $variable;
      $variable_short =~ s/\{.*\}//;
      $variable_short =~ s/\[.*\]//;
      $this->{COMMENTS}->{$variable_short} = $variables{$variable}->[2]
         if ( ! defined $this->{COMMENTS}->{$variable_short} );


      $value = $variables{$variable}->[1];
      $variable_type = $variables{$variable}->[3];
      if ( $variable_type == $VARIABLE_SINGLE_QUOTES || 
           $variable_type == $VARIABLE_DOUBLE_QUOTES )
      {
        $this->{GRID}->SetCellEditor($row,1, Wx::GridCellTextEditor->new());
      }
      elsif ( $variable_type == $VARIABLE_FLOAT )
      {
        $this->{GRID}->SetCellEditor($row,1, Wx::GridCellFloatEditor->new());
      }
      elsif ( $variable_type == $VARIABLE_BOOL )
      {
        $this->{GRID}->SetCellEditor($row,1, Wx::GridCellBoolEditor->new());
      }
      elsif ( $variable_type == $VARIABLE_INTEGER )
      {
        $this->{GRID}->SetCellEditor($row,1, Wx::GridCellNumberEditor->new());
      }
      $this->{GRID}->SetCellValue($row,0, $variable);
      $this->{GRID}->SetCellValue($row,1, $value);
      $row++;
  }
}

# pretty printer for Wx::GridEvent
sub G2S {
  my $event = shift;
  my( $x, $y ) = ( $event->GetCol, $event->GetRow );

  return "( $x, $y )";
}

# prety printer for Wx::GridSizeEvent
sub GS2S {
  my $event = shift;
  my $roc = $event->GetRowOrCol;

  return "( $roc )";
}

# creates an anonymous sub that logs and skips any grid event
sub c_log_skip {
  my $text = shift;

  return sub {
    Wx::LogMessage( "%s %s", $text, G2S( $_[1] ) );
    $_[1]->Skip;
  };
}

package MyPlSizerFrame;

use strict;
use vars qw(@ISA);

@ISA = qw(Wx::Frame);

use Wx qw(:sizer);

sub new {
  my $class = shift;
  my $this = $class->SUPER::new( $_[0], -1, $_[1], [ @_[2,3] ] );

  my $panel = Wx::Panel->new( $this, -1 );
  my $s = Wx::BoxSizer->new( wxVERTICAL );

  $this->{HTML} = Wx::HtmlWindow->new( $panel, -1 );
  $s->Add( $this->{HTML}, 2, wxGROW|wxALL, 20 );


  #my $button = Wx::Button->new( $panel, -1, "Button1" );
  #$s->Add( Wx::Button->new( $panel, -1, "Button1" ), 1, wxGROW|wxALL, 5 );

  $panel->SetSizer( $s );
  $panel->SetAutoLayout( 1 );

  $this;
}

sub LoadPage
{
  my $self = shift;
  my $file = shift;
  
  my $description = ::extractTable($file);

        my $status_page = "
<html>
<head>
  <title>Help</title>
</head>
<body>
<p>
$description
</p>
</body>
</html>
";
  $self->{HTML}->SetPage($status_page);
}

package ThresholdGrid;

use strict;
use vars qw(@ISA); @ISA = qw(Wx::Panel);

use Wx::Event qw(EVT_GRID_CELL_LEFT_CLICK EVT_GRID_CELL_RIGHT_CLICK
    EVT_GRID_CELL_LEFT_DCLICK EVT_GRID_CELL_RIGHT_DCLICK
    EVT_GRID_LABEL_LEFT_CLICK EVT_GRID_LABEL_RIGHT_CLICK
    EVT_GRID_LABEL_LEFT_DCLICK EVT_GRID_LABEL_RIGHT_DCLICK
    EVT_GRID_ROW_SIZE EVT_GRID_COL_SIZE EVT_GRID_RANGE_SELECT
    EVT_GRID_CELL_CHANGE EVT_GRID_SELECT_CELL EVT_BUTTON );

use Wx qw(:sizer);

use Wx qw(wxRED wxBLUE wxGREEN);

use Wx qw(wxID_OK wxDefaultPosition wxDefaultSize wxTE_MULTILINE);


my @valid_resources =
       ( '/.*/', 'undef', '["HEARTBEAT", \'/.*/\']', '["DISCOVER", \'/.*/\']' );

my @valid_severity =
       (
         '$FT::ESEV[0]',
         '$FT::ESEV[1]',
         '$FT::ESEV[2]',
         '$FT::ESEV[3]',
         '$FT::ESEV[4]',
         '$FT::ESEV[5]',
         '$FT::ESEV[6]',
         '$FT::ESEV[7]',
         '$FT::ESEV[8]',
         '$FT::ESEV[9]',
       );

sub getSelections
{
  no strict 'refs';

  my $package = shift;
  my $suffix = shift;

  my @selections = ();

  my $name;
  my $fullname;

  my $stash = *{$package . '::'}{HASH};

  foreach $name (keys %$stash)
  {

    my $cmp_name = $name;
    $cmp_name =~ s/\[.*\]$//;
    $cmp_name =~ s/\{.*\}$//;
    if ($cmp_name =~ /${suffix}$/ )
    {
      $fullname = "\$" . $name;
      push(@selections, $fullname);
    }
  }

  return @selections;
}

sub new {
  my $class = shift;
  my $this = $class->SUPER::new( $_[0], -1 );

  $this->{GRID} = Wx::Grid->new($this, -1);

  $this->{CURRENT_ROW} = -1;

  my $top_s = Wx::BoxSizer->new(wxVERTICAL);
  my $but_s = Wx::BoxSizer->new(wxHORIZONTAL);

  my $insert_above = Wx::Button->new($this, -1, 'InsertAbove');
  my $insert_below = Wx::Button->new($this, -1, 'InsertBelow');
  my $delete = Wx::Button->new($this, -1, 'Delete');

  $but_s->Add($insert_above);
  $but_s->Add($insert_below);
  $but_s->Add($delete);

  $top_s->Add($this->{GRID}, 1, wxGROW|wxALL, 5);
  $top_s->Add($but_s, 0, wxALL, 5);

  $this->SetSizer($top_s);
  $this->SetAutoLayout(1);

  $this->{GRID}->CreateGrid(1,6);
  $this->{GRID}->SetColLabelValue(0, "Resource");
  $this->{GRID}->SetColLabelValue(1, "Calculation");
  $this->{GRID}->SetColLabelValue(2, "Severity");
  $this->{GRID}->SetColLabelValue(3, "Event ID");
  $this->{GRID}->SetColLabelValue(4, "Event Message");
  $this->{GRID}->SetColLabelValue(5, "Command Action");

  EVT_GRID_CELL_LEFT_CLICK( $this, c_log_skip( "Cell left click" ) );
  EVT_GRID_CELL_RIGHT_CLICK( $this, \&CellHelp );
  EVT_GRID_CELL_LEFT_DCLICK( $this, c_log_skip( "Cell left double click" ) );
  EVT_GRID_CELL_RIGHT_DCLICK( $this, c_log_skip( "Cell right double click" ) );
  EVT_GRID_LABEL_LEFT_CLICK( $this, c_log_skip( "Label left click" ) );
  EVT_GRID_LABEL_RIGHT_CLICK( $this, c_log_skip( "Label right click" ) );
  EVT_GRID_LABEL_LEFT_DCLICK( $this, \&CellDuplicate );
  EVT_GRID_LABEL_RIGHT_DCLICK( $this, c_log_skip( "Label right double click" ) );

  EVT_GRID_ROW_SIZE( $this, sub {
                       Wx::LogMessage( "%s %s", "Row size", GS2S( $_[1] ) );
                       $_[1]->Skip;
                     } );
  EVT_GRID_COL_SIZE( $this, sub {
                       Wx::LogMessage( "%s %s", "Col size", GS2S( $_[1] ) );
                       $_[1]->Skip;
                     } );

  EVT_GRID_RANGE_SELECT( $this, sub {
                           Wx::LogMessage( "Range %sselect (%d, %d, %d, %d)",
                                           ( $_[1]->Selecting ? '' : 'de' ),
                                           $_[1]->GetLeftCol, $_[1]->GetTopRow,
                                           $_[1]->GetRightCol,
                                           $_[1]->GetBottomRow );
                           $_[1]->Skip;
                         } );
  EVT_GRID_CELL_CHANGE( $this, c_log_skip( "Cell content changed" ) );
  EVT_GRID_SELECT_CELL( $this,  sub {
                       my( $col, $row ) = ( $_[1]->GetCol, $_[1]->GetRow );
                       Wx::LogMessage( "select %s %s", $row, $col );
                       $this->{CURRENT_ROW} = $row;
                       $_[1]->Skip;
                         } );

  EVT_BUTTON( $this, $insert_above, \&InsertRowAbove);
  EVT_BUTTON( $this, $insert_below, \&InsertRowBelow);
  EVT_BUTTON( $this, $delete, \&DeleteRow);

  return $this;
}


sub CellDuplicate
{
  my $this = shift;
  my $event = shift;
  use Wx qw(wxOK);

  my( $col, $row ) = ( $event->GetCol, $event->GetRow );
  $this->{GRID}->InsertRows($row + 1);

  my $num_cols = $this->{GRID}->GetNumberCols();
  my $value;
  for ( $col=0; $col <  $num_cols; $col++ )
  {
    $value = $this->{GRID}->GetCellValueXY($row, $col);
    $this->{GRID}->SetCellValue($row + 1,$col, $value);
  }

      $this->{GRID}->SetCellEditor( 
	  $row + 1 , 0, Wx::GridCellChoiceEditor->new([@valid_resources], 1));

      $this->{GRID}->SetCellEditor( 
	  $row + 1 , 2, Wx::GridCellChoiceEditor->new([@valid_severity], 1));

      my @selections = getSelections($FT::PACKAGE, "_ID");
      $this->{GRID}->SetCellEditor( 
	  $row + 1 , 3, Wx::GridCellChoiceEditor->new([@selections], 1));

      @selections = getSelections($FT::PACKAGE, "_MSG");
      $this->{GRID}->SetCellEditor( 
	  $row + 1 , 4, Wx::GridCellChoiceEditor->new([@selections], 1));
	
      @selections = getSelections($FT::PACKAGE, "_CMD");
      $this->{GRID}->SetCellEditor( 
	  $row + 1 , 5, Wx::GridCellChoiceEditor->new([@selections], 1));
}

sub SeverityHelp {
  my( $this, $event ) = @_;
  my( $frame ) = MyPlSizerFrame->new( $this, 'Severity Help', 200, 200 );
  $frame->LoadPage("$main::HTML_DIR/event_manager.html");
  $frame->Show( 1 );


}

sub CellHelp
{
  my $this = shift;
  my $event = shift;
  use Wx qw(wxOK);
  use Wx qw(wxID_OK wxTE_MULTILINE  wxDefaultSize);

  my( $col, $row ) = ( $event->GetCol, $event->GetRow );
  my $variable = $this->{GRID}->GetCellValueXY($row, $col);
  $variable =~ s/^\$//;

  my $variable_short = $variable;
  $variable_short =~ s/\{.*\}//;
  $variable_short =~ s/\[.*\]//;

  if ( $col == 2 )
  {
    $this->SeverityHelp($event);


    return;
  }


  my $value = "undefined";

  my $comments = "";
  $comments = $this->{COMMENTS}->{$variable_short}
      if ( exists $this->{COMMENTS}->{$variable_short} );

  if ( $variable =~ /^[A-Z_]/ )
  {
    if ( $variable =~ /::/ )
    {
      eval "\$value = \$$variable";
    }
    else
    {
      eval "\$value = \$${FT::PACKAGE}::$variable";
    }
    if ( ref($value) eq "ARRAY" )
    {
      my $new_value = "[ ";
      foreach (@$value)
      {
        $new_value = $new_value . $_ . ", ";
      }
      $new_value =~ s/, $/\]/;
      $value = $new_value;
    }
    Wx::MessageBox( 
	 "( $value ): " . $comments,
         $variable, wxOK, $this );
  }
}

sub InsertRowAbove
{
  my $this = shift;
  my $event = shift;

  if ( defined $this->{CURRENT_ROW} )
  {
    my $row = $this->{CURRENT_ROW};
    $this->{GRID}->InsertRows($row);
    Wx::LogMessage( "insert row");
  }
}

sub InsertRowBelow
{
  my $this = shift;
  my $event = shift;

  if ( defined $this->{CURRENT_ROW} )
  {
    my $row = $this->{CURRENT_ROW};
    $this->{GRID}->InsertRows($row + 1);

    $this->{GRID}->SetCellEditor( 
	  $row + 1 , 0, Wx::GridCellChoiceEditor->new([@valid_resources], 1));

    $this->{GRID}->SetCellEditor( 
	  $row + 1 , 2, Wx::GridCellChoiceEditor->new([@valid_severity], 1));

    my @selections = getSelections($FT::PACKAGE, "_ID");
    $this->{GRID}->SetCellEditor( 
	  $row + 1 , 3, Wx::GridCellChoiceEditor->new([@selections], 1));

    @selections = getSelections($FT::PACKAGE, "_MSG");
    $this->{GRID}->SetCellEditor( 
	  $row + 1 , 4, Wx::GridCellChoiceEditor->new([@selections], 1));
	
    @selections = getSelections($FT::PACKAGE, "_CMD");
    $this->{GRID}->SetCellEditor( 
	  $row + 1 , 5, Wx::GridCellChoiceEditor->new([@selections], 1));
	
    Wx::LogMessage( "insert row");
  }
}

sub DeleteRow
{
  my $this = shift;
  my $event = shift;

  my $num_rows = $this->{GRID}->GetNumberRows();
  if ( $num_rows && $this->{CURRENT_ROW} != -1 )
  {
    my $row = $this->{CURRENT_ROW};
    $this->{GRID}->DeleteRows($row);
    $this->{CURRENT_ROW} = -1;
    Wx::LogMessage( "delete row");
  }
}

sub loadGrid
{
  my $this = shift;
  my $file = shift;
  my $thresholds = shift;

  $this->{CONFIG_FILE} = $file;
  $this->{GRID}->DeleteCols(0, 6);
  $this->{GRID}->DeleteRows(0, 500);

  my $num_thresholds = @$thresholds;
  $this->{GRID}->InsertRows(0, $num_thresholds);
  $this->{GRID}->InsertCols(0, 6);

  $this->{GRID}->SetColSize(0, 150);
  $this->{GRID}->SetColSize(1, 200);
  $this->{GRID}->SetColSize(3, 150);
  $this->{GRID}->SetColSize(4, 150);
  $this->{GRID}->SetColSize(5, 100);

  my $threshold;
  my $row = 0;
  my $field;
  foreach $threshold (@$thresholds)
  {
      my $col = 0;


      $this->{GRID}->SetCellEditor( 
	  $row , 0, Wx::GridCellChoiceEditor->new([@valid_resources], 1));

      $this->{GRID}->SetCellEditor( 
	  $row , 2, Wx::GridCellChoiceEditor->new([@valid_severity], 1));

      my @selections = getSelections($FT::PACKAGE, "_ID");
      $this->{GRID}->SetCellEditor( 
	  $row , 3, Wx::GridCellChoiceEditor->new([@selections], 1));

      @selections = getSelections($FT::PACKAGE, "_MSG");
      $this->{GRID}->SetCellEditor( 
	  $row , 4, Wx::GridCellChoiceEditor->new([@selections], 1));
	
      @selections = getSelections($FT::PACKAGE, "_CMD");
      $this->{GRID}->SetCellEditor( 
	  $row , 5, Wx::GridCellChoiceEditor->new([@selections], 1));

      # push ( @{$this->{CALCULATIONS}}, $threshold->[1]);

      foreach $field (@$threshold)
      {
	$this->{GRID}->SetCellValue($row,$col, $field);
	$col++;
      }
      $row++;
  }

  $this->{GRID}->ClearSelection();
  $this->{GRID}->ClearSelection();
}

# pretty printer for Wx::GridEvent
sub G2S {
  my $event = shift;
  my( $x, $y ) = ( $event->GetCol, $event->GetRow );

  return "( $x, $y )";
}

# prety printer for Wx::GridSizeEvent
sub GS2S {
  my $event = shift;
  my $roc = $event->GetRowOrCol;

  return "( $roc )";
}

# creates an anonymous sub that logs and skips any grid event
sub c_log_skip {
  my $text = shift;

  return sub {
    Wx::LogMessage( "%s %s", $text, G2S( $_[1] ) );
    $_[1]->Skip;
  };
}


package HtmlWindowWin;

use vars qw(@ISA); @ISA = qw(Wx::Panel);
use Wx qw(:sizer);
use Wx::Event qw(EVT_BUTTON);

sub new {
  my $class = shift;
  my $this = $class->SUPER::new( $_[0], -1 );

  my $html = $this->{HTML} = Wx::HtmlWindow->new( $this, -1 );
  my $top_s = Wx::BoxSizer->new( wxVERTICAL );

  my $but_s = Wx::BoxSizer->new( wxHORIZONTAL );
  my $print = Wx::Button->new( $this, -1, 'Print' );
  my $forward = Wx::Button->new( $this, -1, 'Forward' );
  my $back = Wx::Button->new( $this, -1, 'Back' );
  my $preview = Wx::Button->new( $this, -1, 'Preview' );
  my $pages = Wx::Button->new( $this, -1, 'Page Setup' );
  my $prints = Wx::Button->new( $this, -1, 'Printer Setup' );

  $but_s->Add( $back );
  $but_s->Add( $forward );
  $but_s->Add( $preview );
  $but_s->Add( $print );
  $but_s->Add( $pages );
  $but_s->Add( $prints );

  $top_s->Add( $html, 1, wxGROW|wxALL, 5 );
  $top_s->Add( $but_s, 0, wxALL, 5 );

  $this->SetSizer( $top_s );
  $this->SetAutoLayout( 1 );

  EVT_BUTTON( $this, $print, \&OnPrint );
  EVT_BUTTON( $this, $preview, \&OnPreview );
  EVT_BUTTON( $this, $forward, \&OnForward );
  EVT_BUTTON( $this, $back, \&OnBack );
  EVT_BUTTON( $this, $pages, \&OnPageSetup );
  EVT_BUTTON( $this, $prints, \&OnPrinterSetup );

  $this->{PRINTER} = Wx::HtmlEasyPrinting->new( 'wxPerl demo' );

  return $this;
}

sub LoadPage 
{
  my $this = shift;
  my $html = shift;
  
  $this->{HTML}->LoadPage($html);
}

sub html { $_[0]->{HTML} }
sub printer { $_[0]->{PRINTER} }

sub OnPrint {
  my( $this, $event ) = @_;

  $this->printer->PrintFile( $this->html->GetOpenedPage );
}

sub OnPageSetup {
  my $this = shift;

  $this->printer->PageSetup();
}

sub OnPrinterSetup {
  my $this = shift;

  $this->printer->PrinterSetup();
}

sub OnPreview {
  my( $this, $event ) = @_;

  $this->printer->PreviewFile( $this->html->GetOpenedPage );
}

sub OnForward {
  my( $this, $event ) = @_;

  $this->html->HistoryForward();
}

sub OnBack {
  my( $this, $event ) = @_;

  $this->html->HistoryBack();
}


package main;

push(@INC, "./");

my $app = MyApp->new;


$app->MainLoop;

# Local variables: #
# mode: cperl #
# End: #
