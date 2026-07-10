package FTMON::Helper;
#############################################################################
#                    FTMON (Fast Track Systems Monitor)
#
#   Script: @(#) $RCSfile: Helper.pm,v $ 
# 
#   DESCRIPTION: 
#
#   @(#) Defines usefull functions for use by all FTMON modules.
#
#   $Source: /cvsroot/ftmon/base2/lib/FTMON/Helper.pm,v $
#
#   $Date: 2003/04/18 14:06:19 $
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
#      Sydney NSW 2122
#      AUSTRALIA
#      dsheehan@ftmon.org
#      http://ftmon.org
#
#############################################################################
require 5.002;

use FTMON::Scheduler;



BEGIN
{

	my $http_page_begin_fmt_str = 
"<HTML>
<meta HTTP-EQUIV=\"Refresh\" CONTENT=\"%s\">
<BODY BGCOLOR=\"#FFFFFF\">
&nbsp;
<H1><C ALIGN=\"CENTER\"><A HREF=\"http://www.ftmon.org\">
%s
</A> %s (%s)</C></H1>
";

	my $http_page_end_fmt_str = 
'&nbsp
</BODY>
</HTML>
';
	  
	# -------------------------------------------------------------------------
	sub http_page_begin
	{
	  local(*FH, 
	        $title,
	        $refresh,
		$description,
	        $root_path) = @_;

	   $root_path = "/ftmon" if ( ! defined $root_path );
	   $refresh = 60 if ( ! defined $refresh );
	   $description = "" if ( ! defined $description );
	   # REVISIT: Make stand alone.
	   my $date_str = $FT::dd . " " .
	                  $FT::Mth . " " .
	                  $FT::yyyy . " " .
	                  $FT::hh . ":" .
	                  $FT::mm . ":" .
	                  $FT::ss;

	   my $jpg = "$root_path/ftmon2_small.jpg";
           $jpg = "<IMG BORDER=\"0\" IMG SRC=\"$jpg\">" if ( $jpg );

	   printf( FH  $http_page_begin_fmt_str, 
	     $refresh, 
	     $jpg,
	     "",
	     $date_str);
	   #$title, 

           my $selections =
            "<A HREF=\"" . $root_path . 
	             "index.html\">[products]</A>&nbsp\n" .
             "<A HREF=\"" . $root_path . 
	             "events.html\">[events]</A>&nbsp\n" .
             "<A HREF=\"" . $root_path . 
	             "history.html\">[history]</A>&nbsp\n" .
             "<A HREF=\"" . $root_path . 
	             "jobs.html\">[jobs]</A>&nbsp\n" .
             "<A HREF=\"" . $root_path . 
	             "event_manager.html\">[event_manager]</A>&nbsp\n" .
             "<A HREF=\"" . $root_path . 
	             "help.html\">[help]</A>&nbsp\n";

           $selections =~ s/\[$title\]/<b>\[$title\]<\/b>/;

           printf FH $selections;

	   printf  FH  "<P>$description</P>\n";
	}

	# -------------------------------------------------------------------------
	sub http_page_end
	{
	  local(*FH) = @_;

	  print FH $http_page_end_fmt_str;
	}



# '  <TABLE BORDER=1 COLS=%s WIDTH="900" CELLPADDING="0" CELLSPACING="3"
# ALIGN="center" BGCOLOR="#cccccc">
	my $http_table_start = 
'  <TABLE WIDTH=800 ALIGN="JUSTIFY" BORDER=1 COLS=%s CELLPADDING="0" CELLSPACING="3"
BGCOLOR="#eeeeee">
   <CAPTION><b>%s</b></CAPTION>
'; 

	my $http_table_end = 
'  </TABLE>
';


	my $http_table_header_fmt_str = 
'<TD><CENTER><H3><FONT SIZE=-1 COLOR="%s">%s</FONT></H3></CENTER></TD>
       
';


	# -------------------------------------------------------------------------
	sub http_table_start
	{
	  local(*FH, 
	        $caption,
	        $col_names,
		      $color
	        ) = @_;

	   $color = '#CC0000' if ( ! defined($color) );

	   my $col_name = "";
	   my $num_cols = @$col_names;
	   printf(FH $http_table_start, $num_cols, $caption);

	   print FH "    <TR ALIGN=\"JUSTIFY\">\n";

	   $col_name = "";

	   foreach $col_name ( @{$col_names} )
	   {
	     printf(FH  $http_table_header_fmt_str, $color, $col_name);
	   }

	   print FH "    </TR>\n";
	}

	# -------------------------------------------------------------------------
	sub http_table_end
	{
	  local(*FH) = @_;
	  print FH $http_table_end;
	}


	# -------------------------------------------------------------------------
	sub print_table
	{
		local(*FH, 
					$list, 
					$font_size,
					$fmt_str, 
					$max_rows  ) = @_;

	   $max_rows = @$list if ( ! defined($max_rows) && defined @$list );
		 $max_rows = 0 if ( ! defined $max_rows );

		 $font_size = "-1" if ( ! defined($font_size) );

	   my $i;
	   if ( ! defined($fmt_str) || ! $fmt_str )
	   {
		   $fmt_str = "    <TR ALIGN=\"JUSTIFY\">";

	     my $cols = @{$list->[0]};
		   my @max_width;

	     for ( $j = 0; $j < $max_rows; $j++ )
			 {
	       for ( $i = 0; $i < $cols; $i++ )
	       {
				   my $cell = $list->[$j]->[$i];
					 $cell =~ s/<.*>//g;
					 my $length = length($cell);
					 if ( ! defined $max_width[$i] || 
					      $max_width[$i] < $length )
					 {
					   $max_width[$i] = $length;
					 }
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


			 my $header_str = "";
	     for ( $i = 0; $i < $cols; $i++ )
	     {
				 if ( $i == 0 )
				 {
			     $fmt_str = 
				      $fmt_str .  
				      '  <TD ALIGN="JUSTIFY" WIDTH="' .
						  ( $max_width[$i] ) .
						  '%%"><FONT SIZE=' . $font_size . '><CENTER>&nbsp; %s </CENTER></FONT></TD>';
				 }
				 else
				 {
			     $fmt_str = 
				     $fmt_str .  
				      '  <TD ALIGN="JUSTIFY" WIDTH="' .
						  ( $max_width[$i] + 1 ) .
						  '%%"><FONT SIZE=' . $font_size . '><LEFT>&nbsp; %s </LEFT></FONT></TD>';
				 }
	     }
		   $fmt_str = $fmt_str . '    </TR>';
	   }

	   for ( $i = 0; $i < $max_rows; $i++ )
	   {
       my @my_list = @{$list->[$i]};
		   printf( FH  $fmt_str, @my_list ) if defined $list->[$i];
	   }

	   print FH "\n";
	}
# -------------------------------------------------------------------------
	sub print_table_new
	{
		local(*FH, 
					$list, 
					$font_size,
					$fmt_str, 
					$max_rows  ) = @_;

	   $max_rows = @$list if ( ! defined($max_rows) && defined @$list );
		 $max_rows = 0 if ( ! defined $max_rows );

		 $font_size = "+0" if ( ! defined($font_size) );

	   my $i;
		 my $is_fmt_str = 1;
	   if ( ! defined($fmt_str) || ! $fmt_str )
	   {
		   $is_fmt_str = 0;
		   $fmt_str = "    <TR ALIGN=\"JUSTIFY\">";

	     my $cols = @{$list->[0]};
	     for ( $i = 0; $i < $cols; $i++ )
	     {

				 if ( $i == 0 )
				 {
			     $fmt_str = 
				    $fmt_str .  
				    '      <TD ALIGN="JUSTIFY" WIDTH="%s%%"><FONT SIZE=' . $font_size . '><CENTER> %s </CENTER></FONT></TD>';
				 }
				 else
				 {
			     $fmt_str = 
				    $fmt_str .  
				    '      <TD ALIGN="JUSTIFY" WIDTH="%s%%"><FONT SIZE=' . $font_size . '><LEFT> %s </LEFT></FONT></TD>';
				 }
	     }
		   $fmt_str = $fmt_str . '    </TR>';
	   }

		      
		 my @new_list;
		 my $total_width;
	   for ( $i = 0; $i < $max_rows; $i++ )
	   {
				 if ( $is_fmt_str )
				 {
	         printf( FH  $fmt_str, @{$list->[$i]} );
				 }
				 else
				 {
		       @new_list = ();

		       $total_width = 0;
	         foreach ( @{$list->[$i]} ) 
				   {
				     $total_width = $total_width + length($_);
				   }

					 my $cell;
	         foreach $cell ( @{$list->[$i]} ) 
				   {
						 my $tds_length = 100 * length($cell)/$total_width;
						 if ( $cell =~ /<TD>/ )
						 {
							 $cell =~ s/<TD>\s+<TD>/<TD>/;
							 $cell =~ s/<\/TD>\s+<\/TD>/<\/TD>/;
						   my @tds = split(/<TD>|<\/TD>/, $cell);
							 my $tds;
							 my $tds_length = 0;
							 $cell = "";
							 foreach $tds (@tds)
							 {
								 my $strip_tds = $tds;
								 #$strip_tds =~ s/<.*?>//g;
							   $tds_length = $tds_length + length($stip_tds);
							 }
							 foreach $tds (@tds)
							 {
								 my $strip_tds = $tds;
								 #$strip_tds =~ s/<.*?>//g;
								 my $width = 1;
								 if ($tds_length != 0 )
								 {
								   $width = 100 * length($strip_tds) / $tds_length;
								 }
								 $cell = $cell + 
								         '<TD WIDTH="' + $width + '%">' + $strip_tds + '</TD>';
							 }
						 }
				     push(@new_list, $tds_length);
				     push(@new_list, $cell);
				   }

	        printf( FH  $fmt_str, @new_list ) ;
				 }
	   }

	   print FH "\n";
	}

  sub cfg_error
  {
    print "REVISIT:  cfg_error \n";
  }


sub file_copy
{
  my( $l_old_file, $l_new_file ) = @_;

  my( $l_warning_old ) = $^W;
  $^W = 0;

  if ( ! open( OLD, "< $l_old_file") )
  {
    die "can't open $l_old_file: $!";
  }

  if ( ! open( NEW, "> $l_new_file") )
  {
    die "can't open $l_new_file: $!";
  }

  binmode(OLD);
  binmode(NEW);
  my $blksize = (stat(OLD))[11] || 16384;
  my $len;
  my $buf;
  my $offset;
  my $written;
  while ( $len = sysread(OLD, $buf, $blksize) )
  {
    if ( ! defined($len) )
    {
      next if ( $! =~ /^Interrupted/ );
      die "System read error: $!";
    }

    $offset = 0;
    while ( $len ) 
    {
      if ( ! defined(($written = syswrite(NEW, $buf, $len, $offset))) )
      {
        die "System write error: $!";
      }
      $len    -= $written;
      $offset += $written;
    }
  }

  if ( ! close( OLD ) )
  {
    die __LINE__ . " : Could not close $l_old_file - $!";
  }

  if ( ! close( NEW ) )
  {
    die __LINE__ . " : Could not close $l_new_file - $!";
  }

  $^W = $l_warning_old; 
}


};

1;
