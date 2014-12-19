#!/usr/bin/env python

# Python Standard Modules
import argparse
import collections
import logging
import os
import re
import sys

# Append mugqic_pipelines directory to Python library path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(sys.argv[0])))))

# MUGQIC Modules
from core.config import *
from core.job import *
from core.pipeline import *
from bfx.design import *
from bfx.readset import *

from bfx import bedtools
from bfx import cufflinks
from bfx import differential_expression
from bfx import htseq
from bfx import metrics
from bfx import picard
from bfx import samtools
from bfx import star
from bfx import gq_seq_utils
from pipelines import common
import utils

log = logging.getLogger(__name__)

class RnaSeq(common.Illumina):
    """
    RNA-Seq Pipeline
    ================

    The standard MUGQIC RNA-Seq pipeline uses [STAR](https://code.google.com/p/rna-star/) to align reads
    to the reference genome and discover transcript splice sites. These alignments are used during
    downstream analysis in several ways. [Cufflinks](http://cufflinks.cbcb.umd.edu/) uses this map
    against the reference genome to assemble the reads into transcripts. Cufflinks is executed in two different ways:

    * Cufflinks_denovo: Cufflinks construct a parsimonious set of transcripts that "explain" the reads
    observed in the RNA-Seq experiment.
    * Cufflinks_known: A reference annotation file is supplied to estimate isoform expression.
    Cufflinks will not assemble novel transcripts, and the program will ignore alignments not structurally
    compatible with any reference transcript.

    Cuffdiff, a part of the Cufflinks package, takes the aligned reads from two conditions and reports transcripts
    that are differentially expressed. FPKM values calculated by cufflinks are used as input of
    the differential transcript expression analysis. This differences are reported in the isoform_exp.diff.with.fpkm.csv    files located in the cuffdiff_denovo and cuffdiff_known directories, one directory per design.
    Otherwise, differential gene expression uses raw read counts generated by HTSeq as input.
    The *DESeq* and *edgeR* methods model **count data** by a negative binomial distribution. The parameters
    of the distribution (mean and dispersion) are estimated from the data, i.e. from the read counts
    in the input files. Both methods compute a measure of read abundance, i.e. expression level (called
    *base mean* or *mean of normalized counts* in DESeq, and *concentration* in edgeR) for each gene
    and apply a hypothesis test to each gene to evaluate differential expression. In particular,
    both methods determine a p-value and a log2 fold change (in expression level) for each gene.
    The Log2 FC of EdgeR is reported in the differential gene results file, one file per design.

    The log2fold change is the logarithm (to basis 2) of the fold change condition from condition A to B
    (mutation or treatment are the most common conditions). A "fold change" between conditions A and B
    at a gene or transcript is normally computed as the ratio at gene or transcript of the base mean
    of scaled counts for condition B to the base mean of scaled counts for condition A. Counts are scaled
    by a size factor in a step called normalisation (if the counts of non-differentially expressed genes
    in one sample are, on average, twice as high as in another, the size factor for the first sample
    should be twice that of the other sample). Each column of the count table is then divided
    by the size factor for this column and the count values are brought to a common scale, making them comparable.
    See the [EdgeR vignette](http://www.bioconductor.org/packages/2.12/bioc/vignettes/edgeR/inst/doc/edgeR.pdf)
    for additional information on normalization approaches used in the pipeline.

    A summary html report is automatically generated by the pipeline. This report contains description
    of the sequencing experiment as well as a detailed presentation of the pipeline steps and results.
    Various Quality Control (QC) summary statistics are included in the report and additional QC analysis
    is accessible for download directly through the report. The report includes also the main references
    of the software and methods used during the analysis, together with the full list of parameters
    that have been passed to the pipeline main script.

    An example of the RNA-Seq report for an analysis on Public Corriel CEPH B-cell is available for illustration
    purpose only: [RNA-Seq report](http://gqinnovationcenter.com/services/bioinformatics/tools/rnaReport/index.html).

    [Here](https://bitbucket.org/mugqic/mugqic_pipelines/downloads/MUGQIC_Bioinfo_RNA-Seq.pptx) is more
    information about RNA-Seq pipeline that you may find interesting.
    """

    def __init__(self):
        # Add pipeline specific arguments
        self.argparser.add_argument("-d", "--design", help="design file", type=file)
        super(RnaSeq, self).__init__()

    def star(self):
        """
        The filtered reads are aligned to a reference genome. The alignment is done per readset of sequencing
        using the [STAR](https://code.google.com/p/rna-star/) software. It generates a Binary Alignment Map file (.bam).

        This step takes as input files:

        1. Trimmed FASTQ files if available
        2. Else, FASTQ files from the readset file if available
        3. Else, FASTQ output files from previous picard_sam_to_fastq conversion of BAM files
        """

        jobs = []
        project_index_directory = "reference.Merged"
        project_junction_file =  os.path.join("alignment_1stPass", "AllSamples.SJ.out.tab")
        individual_junction_list=[]
        ######
        #pass 1 -alignment
        for readset in self.readsets:
            trim_file_prefix = os.path.join("trim", readset.sample.name, readset.name + ".trim.")
            alignment_1stPass_directory = os.path.join("alignment_1stPass", readset.sample.name, readset.name)
            individual_junction_list.append(os.path.join(alignment_1stPass_directory,"SJ.out.tab"))

            if readset.run_type == "PAIRED_END":
                candidate_input_files = [[trim_file_prefix + "pair1.fastq.gz", trim_file_prefix + "pair2.fastq.gz"]]
                if readset.fastq1 and readset.fastq2:
                    candidate_input_files.append([readset.fastq1, readset.fastq2])
                if readset.bam:
                    candidate_input_files.append([re.sub("\.bam$", ".pair1.fastq.gz", readset.bam), re.sub("\.bam$", ".pair2.fastq.gz", readset.bam)])
                [fastq1, fastq2] = self.select_input_files(candidate_input_files)
            elif readset.run_type == "SINGLE_END":
                candidate_input_files = [[trim_file_prefix + "single.fastq.gz"]]
                if readset.fastq1:
                    candidate_input_files.append([readset.fastq1])
                if readset.bam:
                    candidate_input_files.append([re.sub("\.bam$", ".single.fastq.gz")])
                [fastq1] = self.select_input_files(candidate_input_files)
                fastq2 = None
            else:
                raise Exception("Error: run type \"" + readset.run_type +
                "\" is invalid for readset \"" + readset.name + "\" (should be PAIRED_END or SINGLE_END)!")

            rg_platform = config.param('star_align', 'platform', required=False)
            rg_center = config.param('star_align', 'sequencing_center', required=False)

            job = star.align(
                reads1=fastq1,
                reads2=fastq2,
                output_directory=alignment_1stPass_directory,
                genome_index_folder=None,
                rg_id=readset.name,
                rg_sample=readset.sample.name,
                rg_library=readset.library if readset.library else "",
                rg_platform_unit=readset.run + "_" + readset.lane if readset.run and readset.lane else "",
                rg_platform=rg_platform if rg_platform else "",
                rg_center=rg_center if rg_center else ""
            )
            job.name = "star_align.1." + readset.name
            jobs.append(job)
        
        ######
        jobs.append(concat_jobs([
        #pass 1 - contatenate junction
        star.concatenate_junction(
            input_junction_files_list=individual_junction_list,
            output_junction_file=project_junction_file
        ),
        #pass 1 - genome indexing
        star.index(
            genome_index_folder=project_index_directory,
            junction_file=project_junction_file
        )], name = "star_index.AllSamples"))

        ######
        #Pass 2 - alignment
        for readset in self.readsets:
            trim_file_prefix = os.path.join("trim", readset.sample.name, readset.name + ".trim.")
            alignment_2ndPass_directory = os.path.join("alignment", readset.sample.name, readset.name)

            if readset.run_type == "PAIRED_END":
                candidate_input_files = [[trim_file_prefix + "pair1.fastq.gz", trim_file_prefix + "pair2.fastq.gz"]]
                if readset.fastq1 and readset.fastq2:
                    candidate_input_files.append([readset.fastq1, readset.fastq2])
                if readset.bam:
                    candidate_input_files.append([re.sub("\.bam$", ".pair1.fastq.gz", readset.bam), re.sub("\.bam$", ".pair2.fastq.gz", readset.bam)])
                [fastq1, fastq2] = self.select_input_files(candidate_input_files)
            elif readset.run_type == "SINGLE_END":
                candidate_input_files = [[trim_file_prefix + "single.fastq.gz"]]
                if readset.fastq1:
                    candidate_input_files.append([readset.fastq1])
                if readset.bam:
                    candidate_input_files.append([re.sub("\.bam$", ".single.fastq.gz")])
                [fastq1] = self.select_input_files(candidate_input_files)
                fastq2 = None
            else:
                raise Exception("Error: run type \"" + readset.run_type +
                "\" is invalid for readset \"" + readset.name + "\" (should be PAIRED_END or SINGLE_END)!")

            rg_platform = config.param('star_align', 'platform', required=False)
            rg_center = config.param('star_align', 'sequencing_center', required=False)

            job = star.align(
                reads1=fastq1,
                reads2=fastq2,
                output_directory=alignment_2ndPass_directory,
                genome_index_folder=project_index_directory,
                rg_id=readset.name,
                rg_sample=readset.sample.name,
                rg_library=readset.library if readset.library else "",
                rg_platform_unit=readset.run + "_" + readset.lane if readset.run and readset.lane else "",
                rg_platform=rg_platform if rg_platform else "",
                rg_center=rg_center if rg_center else "",
                create_wiggle_track=True,
                search_chimeres=True,
                cuff_follow=True,
                sort_bam=True
            )
            job.input_files.append(os.path.join(project_index_directory, "SAindex"))
 
            # If this readset is unique for this sample, further BAM merging is not necessary.
            # Thus, create a sample BAM symlink to the readset BAM.
            # remove older symlink before otherwise it raise an error if the link already exist (in case of redo)
            if len(readset.sample.readsets) == 1:
                readset_bam = os.path.join(alignment_2ndPass_directory, "Aligned.sortedByCoord.out.bam")
                sample_bam = os.path.join("alignment", readset.sample.name ,readset.sample.name + ".sorted.bam")
                job = concat_jobs([
                    job,
                    Job([readset_bam], [sample_bam], command="ln -s -f " + os.path.relpath(readset_bam, os.path.dirname(sample_bam)) + " " + sample_bam, removable_files=[sample_bam])])

            job.name = "star_align.2." + readset.name
            jobs.append(job)

        return jobs

    def picard_merge_sam_files(self):
        """
        BAM readset files are merged into one file per sample. Merge is done using [Picard](http://broadinstitute.github.io/picard/).
        """

        jobs = []
        for sample in self.samples:
            # Skip samples with one readset only, since symlink has been created at align step
            if len(sample.readsets) > 1:
                alignment_directory = os.path.join("alignment", sample.name)
                inputs = [os.path.join(alignment_directory, readset.name, "Aligned.sortedByCoord.out.bam") for readset in sample.readsets]
                output = os.path.join(alignment_directory, sample.name + ".sorted.bam")

                job = picard.merge_sam_files(inputs, output)
                job.name = "picard_merge_sam_files." + sample.name
                jobs.append(job)
        return jobs

    def picard_sort_sam(self):
        """
        The alignment file is reordered (karyotypic) using [Picard](http://broadinstitute.github.io/picard/).
        """

        jobs = []
        for sample in self.samples:
            alignment_file_prefix = os.path.join("alignment", sample.name, sample.name)

            job = picard.sort_sam(
                alignment_file_prefix + ".sorted.bam",
                alignment_file_prefix + ".QueryNameSorted.bam",
                "queryname"
            )
            job.name = "picard_sort_sam." + sample.name
            jobs.append(job)
        return jobs

    def picard_mark_duplicates(self):
        """
        Mark duplicates. Aligned reads per sample are duplicates if they have the same 5' alignment positions
        (for both mates in the case of paired-end reads). All but the best pair (based on alignment score)
        will be marked as a duplicate in the BAM file. Marking duplicates is done using [Picard](http://broadinstitute.github.io/picard/).
        """

        jobs = []
        for sample in self.samples:
            alignment_file_prefix = os.path.join("alignment", sample.name, sample.name + ".sorted.")

            job = picard.mark_duplicates(
                [alignment_file_prefix + "bam"],
                alignment_file_prefix + "mdup.bam",
                alignment_file_prefix + "mdup.metrics"
            )
            job.name = "picard_mark_duplicates." + sample.name
            jobs.append(job)
        return jobs

    def rnaseqc(self):
        """
        Computes a series of quality control metrics using [RNA-SeQC](https://www.broadinstitute.org/cancer/cga/rna-seqc).
        """

        sample_file = os.path.join("alignment", "rnaseqc.samples.txt")
        sample_rows = [[sample.name, os.path.join("alignment", sample.name, sample.name + ".sorted.mdup.bam"), "RNAseq"] for sample in self.samples]
        input_bams = [sample_row[1] for sample_row in sample_rows]
        output_directory = os.path.join("metrics", "rnaseqRep")
        # Use GTF with transcript_id only otherwise RNASeQC fails
        gtf_transcript_id = config.param('rnaseqc', 'gtf_transcript_id', type='filepath')

        job = concat_jobs([
            Job(command="mkdir -p " + output_directory, removable_files=[output_directory]),
            Job(input_bams, [sample_file], command="""\
echo "Sample\tBamFile\tNote
{sample_rows}" \\
  > {sample_file}""".format(sample_rows="\n".join(["\t".join(sample_row) for sample_row in sample_rows]), sample_file=sample_file)),
            metrics.rnaseqc(sample_file, output_directory, self.run_type == "SINGLE_END", gtf_file=gtf_transcript_id),
            Job([], [output_directory + ".zip"], command="zip -r {output_directory}.zip {output_directory}".format(output_directory=output_directory))
        ], name="rnaseqc")

        return [job]

    def wiggle(self):
        """
        Generate wiggle tracks suitable for multiple browsers.
        """

        jobs = []

        for sample in self.samples:
            bam_file_prefix = os.path.join("alignment", sample.name, sample.name + ".sorted.mdup.")
            input_bam = bam_file_prefix + "bam"
            bed_graph_prefix = os.path.join("tracks", sample.name, sample.name)
            big_wig_prefix = os.path.join("tracks", "bigWig", sample.name)

            if config.param('DEFAULT', 'strand_info') != 'fr-unstranded':
                input_bam_f1 = bam_file_prefix + "tmp1.forward.bam"
                input_bam_f2 = bam_file_prefix + "tmp2.forward.bam"
                input_bam_r1 = bam_file_prefix + "tmp1.reverse.bam"
                input_bam_r2 = bam_file_prefix + "tmp2.reverse.bam"
                output_bam_f = bam_file_prefix + "forward.bam"
                output_bam_r = bam_file_prefix + "reverse.bam"

                bam_f_job = concat_jobs([
                    samtools.view(input_bam, input_bam_f1, "-bh -F 256 -f 81"),
                    samtools.view(input_bam, input_bam_f2, "-bh -F 256 -f 161"),
                    picard.merge_sam_files([input_bam_f1, input_bam_f2], output_bam_f),
                    Job(command="rm " + input_bam_f1 + " " + input_bam_f2)
                ], name="wiggle." + sample.name + ".forward_strandspec")
                # Remove temporary-then-deleted files from job output files, otherwise job is never up to date
                bam_f_job.output_files.remove(input_bam_f1)
                bam_f_job.output_files.remove(input_bam_f2)

                bam_r_job = concat_jobs([
                    Job(command="mkdir -p " + os.path.join("tracks", sample.name) + " " + os.path.join("tracks", "bigWig")),
                    samtools.view(input_bam, input_bam_r1, "-bh -F 256 -f 97"),
                    samtools.view(input_bam, input_bam_r2, "-bh -F 256 -f 145"),
                    picard.merge_sam_files([input_bam_r1, input_bam_r2], output_bam_r),
                    Job(command="rm " + input_bam_r1 + " " + input_bam_r2)
                ], name="wiggle." + sample.name + ".reverse_strandspec")
                # Remove temporary-then-deleted files from job output files, otherwise job is never up to date
                bam_r_job.output_files.remove(input_bam_r1)
                bam_r_job.output_files.remove(input_bam_r2)

                jobs.extend([bam_f_job, bam_r_job])

                outputs = [
                    [bed_graph_prefix + ".forward.bedGraph", big_wig_prefix + ".forward.bw"],
                    [bed_graph_prefix + ".reverse.bedGraph", big_wig_prefix + ".reverse.bw"],
                ]
            else:
                outputs = [[bed_graph_prefix + ".bedGraph", big_wig_prefix + ".bw"]]

            for bed_graph_output, big_wig_output in outputs:
                job = concat_jobs([
                    Job(command="mkdir -p " + os.path.join("tracks", sample.name) + " " + os.path.join("tracks", "bigWig"), removable_files=["tracks"]),
                    bedtools.graph(input_bam, bed_graph_output, big_wig_output)
                ], name="wiggle." + re.sub(".bedGraph", "", os.path.basename(bed_graph_output)))
                jobs.append(job)

        return jobs

    def raw_counts(self):
        """
        Count reads in features using [htseq-count](http://www-huber.embl.de/users/anders/HTSeq/doc/count.html).
        """

        jobs = []

        for sample in self.samples:
            alignment_file_prefix = os.path.join("alignment", sample.name, sample.name)
            input_bam = alignment_file_prefix + ".QueryNameSorted.bam"

            # Count reads
            output_count = os.path.join("raw_counts", sample.name + ".readcounts.csv")
            stranded = "no" if config.param('DEFAULT', 'strand_info') == "fr-unstranded" else "reverse"
            job = concat_jobs([
                Job(command="mkdir -p raw_counts"),
                htseq.htseq_count(
                    input_bam,
                    config.param('htseq', 'gtf', type='filepath'),
                    output_count,
                    config.param('htseq', 'options'),
                    stranded
                )
            ], name="htseq_count." + sample.name)
            jobs.append(job)

        return jobs

    def raw_counts_metrics(self):
        """
        Create rawcount matrix, zip the wiggle tracks and create the saturation plots based on standardized read counts.
        """

        jobs = []

        # Create raw count matrix
        output_directory = "DGE"
        read_count_files = [os.path.join("raw_counts", sample.name + ".readcounts.csv") for sample in self.samples]
        output_matrix = os.path.join(output_directory, "rawCountMatrix.csv")

        job = Job(read_count_files, [output_matrix], [['raw_counts_metrics', 'module_mugqic_tools']], name="metrics.matrix")

        job.command = """\
mkdir -p {output_directory} && \\
gtf2tmpMatrix.awk \\
  {reference_gtf} \\
  {output_directory}/tmpMatrix.txt && \\
HEAD='Gene\tSymbol' && \\
for read_count_file in \\
  {read_count_files}
do
  sort -k1,1 $read_count_file > {output_directory}/tmpSort.txt && \\
  join -1 1 -2 1 <(sort -k1,1 {output_directory}/tmpMatrix.txt) {output_directory}/tmpSort.txt > {output_directory}/tmpMatrix.2.txt && \\
  mv {output_directory}/tmpMatrix.2.txt {output_directory}/tmpMatrix.txt && \\
  na=$(basename $read_count_file | cut -d. -f1) && \\
  HEAD="$HEAD\t$na"
done && \\
echo -e $HEAD | cat - {output_directory}/tmpMatrix.txt | tr ' ' '\t' > {output_matrix} && \\
rm {output_directory}/tmpSort.txt {output_directory}/tmpMatrix.txt""".format(
            reference_gtf=config.param('raw_counts_metrics', 'gtf', type='filepath'),
            output_directory=output_directory,
            read_count_files=" \\\n  ".join(read_count_files),
            output_matrix=output_matrix
        )
        jobs.append(job)

        # Create Wiggle tracks archive
        wiggle_directory = os.path.join("tracks", "bigWig")
        wiggle_archive = "tracks.zip"
        big_wig_prefix = os.path.join("tracks", "bigWig", sample.name)
        if config.param('DEFAULT', 'strand_info') != 'fr-unstranded':
            wiggle_files = []
            for sample in self.samples:
                wiggle_files.extend([os.path.join(wiggle_directory, sample.name) + ".forward.bw", os.path.join(wiggle_directory, sample.name) + ".reverse.bw"])
        else:
            wiggle_files = [os.path.join(wiggle_directory, sample.name + ".bw") for sample in self.samples]
        jobs.append(Job(wiggle_files, [wiggle_archive], name="metrics.wigzip", command="zip -r " + wiggle_archive + " " + wiggle_directory))

        # RPKM and Saturation
        count_file = os.path.join("DGE", "rawCountMatrix.csv")
        gene_size_file = config.param('rpkm_saturation', 'gene_size', type='filepath')
        rpkm_directory = "raw_counts"
        saturation_directory = os.path.join("metrics", "saturation")

        job = concat_jobs([
            Job(command="mkdir -p " + saturation_directory),
            metrics.rpkm_saturation(count_file, gene_size_file, rpkm_directory, saturation_directory)
        ], name="rpkm_saturation")
        jobs.append(job)

        return jobs

    def cufflinks(self):
        """
        Compute RNA-Seq data expression using [cufflinks](http://cole-trapnell-lab.github.io/cufflinks/cufflinks/).
        """

        jobs = []
        
        gtf = config.param('cufflinks','gtf', type='filepath')

        for sample in self.samples:
            input_bam = os.path.join("alignment", sample.name, sample.name + ".sorted.mdup.bam")
            output_directory = os.path.join("cufflinks", sample.name)

            # De Novo FPKM
            job = cufflinks.cufflinks(input_bam, output_directory, gtf)
            job.removable_files = ["cufflinks"]
            job.name = "cufflinks."+sample.name
            jobs.append(job)

        return jobs
    
    def cuffmerge(self):
        """
        Merge assemblies into a master transcriptome using [cuffmerge](http://cole-trapnell-lab.github.io/cufflinks/cuffmerge/).
        """

        output_directory = os.path.join("cufflinks", "AllSamples")
        sample_file = os.path.join("cufflinks", "cuffmerge.samples.txt")
        input_gtfs = [os.path.join("cufflinks", sample.name, "transcripts.gtf") for sample in self.samples]
        gtf = config.param('cuffmerge','gtf', type='filepath')
        
        
        job = concat_jobs([
            Job(command="mkdir -p " + output_directory),
            Job(input_gtfs, [sample_file], command="""\
`cat > {sample_file} << END
{sample_rows}
END
  
`""".format(sample_rows="\n".join(input_gtfs), sample_file=sample_file)),
            cufflinks.cuffmerge(sample_file, output_directory, gtf_file=gtf)],
            name="cuffmerge")
        
        return [job]
        
    def cuffquant(self):
        """
        Compute gene and transcript expression profiles using [cuffquant](http://cole-trapnell-lab.github.io/cufflinks/cuffquant/).
        """

        jobs = []
        
        gtf = os.path.join("cufflinks", "AllSamples","merged.gtf")
        
        for sample in self.samples:
            input_bam = os.path.join("alignment", sample.name, sample.name + ".sorted.mdup.bam")
            output_directory = os.path.join("cufflinks", sample.name)

            #Quantification
            job = cufflinks.cuffquant(input_bam, output_directory, gtf)
            job.name = "cuffquant."+sample.name
            jobs.append(job)

        return jobs
    
    def cuffdiff(self):
        """
        [Cuffdiff](http://cole-trapnell-lab.github.io/cufflinks/cuffdiff/), the transcript quantification engine
        of Cufflinks, is used to calculate transcript expression levels in more than one condition
        and test them for significant differences.
        """

        jobs = []

        fpkm_directory = "cufflinks"
        gtf = os.path.join(fpkm_directory, "AllSamples","merged.gtf")


        # Perform cuffdiff on each design contrast
        for contrast in self.contrasts:
            job = cufflinks.cuffdiff(
                # Cuffdiff input is a list of lists of replicate bams per control and per treatment
                [[os.path.join(fpkm_directory, sample.name, "abundances.cxb") for sample in group] for group in contrast.controls, contrast.treatments],
                gtf,
                os.path.join("cuffdiff", contrast.name)
            )
            job.removable_files = ["cuffdiff"]
            job.name = "cuffdiff." + contrast.name
            jobs.append(job)

        return jobs
    
    def cuffnorm(self):
        """
        Normalize RNA-Seq expression levels using [Cuffnorm](http://cole-trapnell-lab.github.io/cufflinks/cuffnorm/).
        """

        jobs = []

        fpkm_directory = "cufflinks"
        gtf = os.path.join(fpkm_directory, "AllSamples","merged.gtf")
        sample_labels = ",".join([sample.name for sample in self.samples])


        # Perform cuffnorm using every samples
        job = cufflinks.cuffnorm([os.path.join(fpkm_directory, sample.name, "abundances.cxb") for sample in self.samples],
             gtf,
             "cuffnorm",sample_labels)
        job.removable_files = ["cuffnorm"]
        job.name = "cuffnorm" 
        jobs.append(job)
        
        return jobs

    def differential_expression(self):
        """
        Performs differential gene expression analysis using [DESEQ](http://bioconductor.org/packages/release/bioc/html/DESeq.html) and [EDGER](http://www.bioconductor.org/packages/release/bioc/html/edgeR.html).
        Merge the results of the analysis in a single csv file.
        """

        # If --design <design_file> option is missing, self.contrasts call will raise an Exception
        if self.contrasts:
            design_file = os.path.relpath(self.args.design.name, self.output_dir)
        output_directory = "DGE"
        count_matrix = os.path.join(output_directory, "rawCountMatrix.csv")

        edger_job = differential_expression.edger(design_file, count_matrix, output_directory)
        edger_job.output_files = [os.path.join(output_directory, contrast.name, "edger_results.csv") for contrast in self.contrasts]

        deseq_job = differential_expression.deseq(design_file, count_matrix, output_directory)
        deseq_job.output_files = [os.path.join(output_directory, contrast.name, "dge_results.csv") for contrast in self.contrasts]

        return [concat_jobs([
            Job(command="mkdir -p " + output_directory),
            edger_job,
            deseq_job
        ], name="differential_expression")]

    def differential_expression_goseq(self):
        """
        Gene Ontology analysis for RNA-seq using the bioconductor's R package [goseq](http://www.bioconductor.org/packages/release/bioc/html/goseq.html).
        Generates GO annotations for differential gene expression analysis.
        """

        jobs = []

        for contrast in self.contrasts:
            # goseq for differential gene expression results
            job = differential_expression.goseq(
                os.path.join("DGE", contrast.name, "dge_results.csv"),
                config.param("differential_expression_goseq", "dge_input_columns"),
                os.path.join("DGE", contrast.name, "gene_ontology_results.csv")
            )
            job.name = "differential_expression_goseq.dge." + contrast.name
            jobs.append(job)

        return jobs

    def gq_seq_utils_exploratory_analysis_rnaseq(self):
        """
        Exploratory analysis using the gqSeqUtils R package.
        """

        sample_fpkm_readcounts = [[
            sample.name,
            os.path.join("cufflinks", sample.name, "isoforms.fpkm_tracking"),
            os.path.join("raw_counts", sample.name + ".readcounts.csv")
        ] for sample in self.samples]

        input_file = os.path.join("exploratory", "exploratory.samples.tsv")

        return [concat_jobs([
            Job(command="mkdir -p exploratory"),
            gq_seq_utils.exploratory_analysis_rnaseq(
                os.path.join("DGE", "rawCountMatrix.csv"),
                "cuffnorm",
                config.param('gq_seq_utils_exploratory_analysis_rnaseq', 'genes', type='filepath'),
                "exploratory"
            )
        ], name="gq_seq_utils_exploratory_analysis_rnaseq")]

    def gq_seq_utils_report(self):
        """
        Generates the standard report. A summary html report contains the description of
        the sequencing experiment as well as a detailed presentation of the pipeline steps and results.
        Various Quality Control (QC) summary statistics are included in the report and additional QC analysis
        is accessible for download directly through the report. The report includes also the main references
        of the software and methods used during the analysis, together with the full list of parameters
        passed to the pipeline main script.
        """

        job = gq_seq_utils.report(
            [config_file.name for config_file in self.args.config],
            self.output_dir,
            "RNAseq",
            self.output_dir
        )
        job.input_files = [
            "metrics/trimming.stats"
        ]
        job.name = "gq_seq_utils_report"
        return [job]

    @property
    def steps(self):
        return [
            self.picard_sam_to_fastq,
            self.trimmomatic,
            self.merge_trimmomatic_stats,
            self.star,
            self.picard_merge_sam_files,
            self.picard_sort_sam,
            self.picard_mark_duplicates,
            self.rnaseqc,
            self.wiggle,
            self.raw_counts,
            self.raw_counts_metrics,
            self.cufflinks,
            self.cuffmerge,
            self.cuffquant,
            self.cuffdiff,
            self.cuffnorm,
            self.differential_expression,
            self.differential_expression_goseq,
            self.gq_seq_utils_exploratory_analysis_rnaseq,
            self.gq_seq_utils_report
        ]

if __name__ == '__main__':
    RnaSeq()
