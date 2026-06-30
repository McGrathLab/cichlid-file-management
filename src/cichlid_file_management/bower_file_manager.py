"""Shared FileManager base for bower-building projects.

``BowerFileManager`` is the middle tier between the lab-wide ``BaseFileManager``
(cloud I/O, environment detection, master-dir resolution, branch_name) and the
per-repo file managers. It owns the directories and helpers shared by BOTH the
tracking and post-hoc analysis repos, so neither depends on the other.

    BaseFileManager      (cichlid-file-management: mechanism + branch_name)
        |
    BowerFileManager     (cichlid-file-management: this file)
        |
        |-- FileManager  (cichlid-bower-tracking: + credentials, Pi capture)
        |-- FileManager  (cichlid-analysis:       + post-hoc-only dirs)

What's here: the analysis-states machinery and the per-project data directories
that tracking WRITES and post-hoc READS. What's NOT here: cloud-I/O primitives
and branch_name (BaseFileManager), credentials and Pi-only dirs (tracking repo),
post-hoc-only output dirs (post-hoc repo).

NOTE: the analysis-state store is the legacy summary CSV (s_dt). If/when you move
to the Pydantic JSON state from the bbe work, the methods below are where that
swap happens -- the responsibility stays shared, only the format changes.
"""

from __future__ import annotations

import pandas as pd

from cichlid_file_management import BaseFileManager


class BowerFileManager(BaseFileManager):
    def __init__(self, *args, analysisID=None, projectID=None, **kwargs):
        # BaseFileManager: environment detection, master-dir resolution, cloud
        # I/O, and branch_name. Align this signature with its real __init__.
        super().__init__(*args, **kwargs)
        self.analysisID = analysisID
        self.projectID = projectID

        self._create_analysis_state_dirs()
        if projectID is not None:
            self.setProjectID(projectID)
        # readAnalysisFile() does cloud I/O, so it is NOT called here -- the caller
        # invokes it once it has confirmed the summary file exists. Keeping the
        # constructor I/O-free lets consumers report a clean "missing file" error.

    # ------------------------------------------------------------------ #
    # Analysis-states (shared: both repos read the summary state)         #
    # ------------------------------------------------------------------ #
    def _create_analysis_state_dirs(self):
        self.localMasterAnalysisDir = self.localMasterDir + "__AnalysisStates/"
        if self.analysisID is not None:
            self.localAnalysisStatesDir = (
                self.localMasterAnalysisDir + self.analysisID + "/")
            self.localSummaryFile = (
                self.localAnalysisStatesDir + self.analysisID + ".csv")

    def readAnalysisFile(self):
        self.downloadData(self.localSummaryFile)
        self.s_dt = pd.read_csv(
            self.localSummaryFile, index_col=0,
            dtype={"Prep": bool, "Depth": bool, "Cluster": str})
        if "DissectionTime" in self.s_dt:
            self.s_dt["DissectionTime"] = pd.to_datetime(self.s_dt.DissectionTime)

    def returnEmpty_s_dt(self, projectID="", tankID=""):
        data = {
            "RunAnalysis": False, "tankID": tankID, "StartingFiles": False,
            "Prep": False, "Depth": False, "Cluster": "VideoIndices: ",
            "ManualAnnotation": 0, "ClusterClassification": False, "Summary": False,
            "videoIDs": "VideoIndices: ", "videoIDsToRun": "VideoIndices: ",
            "videoIDsToAnnotate": "VideoIndices: ", "Notes": "",
        }
        return pd.DataFrame(data, index=pd.Index([projectID], name="projectID"))

    # ------------------------------------------------------------------ #
    # Per-project data (shared: tracking writes these, post-hoc reads)    #
    # ------------------------------------------------------------------ #
    def setProjectID(self, projectID):
        self.projectID = projectID
        self._create_project_dirs(projectID)

    def _create_project_dirs(self, projectID):
        # Project root is keyed by analysisID then projectID, matching the
        # existing __ProjectData layout.
        self.localProjectDir = (
            self.localMasterDir + "__ProjectData/"
            + str(self.analysisID) + "/" + projectID + "/")
        self.localLogfile = self.localProjectDir + "Logfile.txt"
        self.localPrepDir = self.localProjectDir + "PrepFiles/"
        self.localFrameDir = self.localProjectDir + "Frames/"
        self.localVideoDir = self.localProjectDir + "Videos/"
        self.localBackupDir = self.localProjectDir + "Backups/"
        # Add the post-hoc-only project subdirs (Summary/, AllClips/, etc.) when
        # you wire up the analysis repo -- those are shared too and belong here.