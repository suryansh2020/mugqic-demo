#!/usr/bin/env perl

=head1 NAME

I<GqSeqUtils>

=head1 SYNOPSIS

GqSeqUtils-> clientReport()

=head1 DESCRIPTION

B<GqSeqUtils> is a library to access/launch functions from the gqSeqUtils R package


=head1 AUTHOR
B<Mathieu Bourgey> - I<mbourgey@genomequebec.com>

=head1 DEPENDENCY

B<Pod::Usage> Usage and help output.

=cut

package GqSeqUtils;

# Strict Pragmas
#--------------------------
use strict;
use warnings;


#--------------------------

# Add the mugqic_pipeline/lib/ path relative to this Perl script to @INC library search variable
use FindBin;
use lib "$FindBin::Bin";

# Dependencies
#-----------------------
use LoadConfig;

# SUB
#-----------------------
sub clientReport {
  my $rH_cfg        = shift;
  my $iniFilePath   = shift;
  my $projectPath   = shift;
  my $pipelineType  = shift;

# pipeline=        ini.file.path=   report.title=    report.contact=  
# project.path=    report.path=     report.author=   report.css=
  my $pipeline = 'pipeline=\"' . $pipelineType . '\",';

  my $title = "";
  my $titleTMP = LoadConfig::getParam($rH_cfg, 'report', 'projectName', 0);
  if (defined($titleTMP) && !($titleTMP eq "")) {
    $title = 'report.title=\"' . $titleTMP . '\",';
  }
  my $path = "";
  my $pathTMP = LoadConfig::getParam($rH_cfg, 'report', 'report.path', 0);
  if (defined($pathTMP) && !($pathTMP eq "")) {
    $path = 'report.path=\"' . $pathTMP . '\",';
  }
  my $author = "";
  my $authorTMP = LoadConfig::getParam($rH_cfg, 'report', 'report.author', 0);
  if (defined($authorTMP) && !($authorTMP eq "")) {
    $author = 'report.author=\"' . $authorTMP . '\",';
  }
  my $contact = "";
  my $contactTMP = LoadConfig::getParam($rH_cfg, 'report', 'report.contact', 0);
  if (defined($contactTMP) && !($contactTMP eq "")) {
    $contact = 'report.contact=\"' . $contactTMP . '\",';
  }

  my $rO_job = new Job();

  if (!$rO_job->isUp2Date()) {
    my $command;
    $rO_job->addModules($rH_cfg, [['report', 'moduleVersion.cranR']]);
    $command .= ' R --no-save -e \'library(gqSeqUtils);';
    $command .= ' mugqicPipelineReport(';
    $command .= ' ' . $pipeline;
    $command .= ' ' . $title;
    $command .= ' ' . $path;
    $command .= ' ' . $author;
    $command .= ' ' . $contact;
    $command .= ' ini.file.path=\"' . $iniFilePath . '\",';
    $command .= ' project.path=\"' . $projectPath . '\")\'';

    $rO_job->addCommand($command);
  }

  return $rO_job;
}


sub exploratoryRnaAnalysis {
  my $rH_cfg        = shift;
  my $readSetSheet  = shift;
  my $workDirectory = shift;
  my $configFile    = shift;

  my $rO_job = new Job();
  $rO_job->testInputOutputs([$configFile], [$workDirectory . '/exploratory/top_sd_heatmap_log2CPM.pdf']);
  #$rO_job->setUp2Date(0);

  if (!$rO_job->isUp2Date()) {
    my $rscript = 'suppressPackageStartupMessages(library(gqSeqUtils));';
    $rscript .= ' initIllmSeqProject(nanuq.file= \"' . $readSetSheet . '\",overwrite.sheets=FALSE,project.path= \"' . $workDirectory . '\");';
    $rscript .= ' exploratoryRNAseq(project.path= \"' . $workDirectory . '\",ini.file.path = \"' . $configFile . '\");';
    $rscript .= ' print(\"done.\")';
    my $rO_job->addModules($rH_cfg, [['downstreamAnalyses','moduleVersion.cranR']]);
    my $command .= ' Rscript -e ' . '\'' . $rscript . '\'';

    $rO_job->addCommand($command);
  }
  return $rO_job;
}

1;
