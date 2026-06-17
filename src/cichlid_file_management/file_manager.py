import os, subprocess, pdb, platform, shutil
from cichlid_file_management.log_parser import LogParser as LP
import pandas as pd 

class FileManager():
    def __init__(self, analysisID = None, projectID = None, modelID = None, rcloneRemote = 'ptm_dropbox:/', masterDir = 'CoS/BioSci/BioSci-McGrath/Apps/CichlidPiData/', check = False):
        # Identify directory for temporary local files
        self._identifyMasterDirectory()
        # Identify cloud directory for rclone
        self.rcloneRemote = rcloneRemote
        # Make sure cloudMasterDir exists
        output = subprocess.run(['rclone', 'lsf', self.rcloneRemote + masterDir], capture_output = True, encoding = 'utf-8')
        if output.stderr == '':
            self.cloudMasterDir = self.rcloneRemote + masterDir
        else:
            raise Exception('Cant find master directory (' + masterDir + ') in rclone remote (' + rcloneRemote + '')
        # Store analysis state information
        
        # Store branch you are running
        self.branch_name = subprocess.run(['git','rev-parse','--abbrev-ref','HEAD'], capture_output = True).stdout.decode().strip()

        # Read in analysis state information
        if analysisID is not None:
            self.localMasterAnalysisDir = self.localMasterDir + '__AnalysisStates/'
            self.localAnalysisStatesDir = self.localMasterAnalysisDir + analysisID + '/'
            self.localAnalysisOutPicsDir = self.localAnalysisStatesDir + 'OutPics/'
            self.localSummaryFile = self.localAnalysisStatesDir + analysisID + '.csv'
            self.localAnalysisFinalDataDir = self.localAnalysisStatesDir + 'SummarizedBuilds/'

            if not self.checkFileExists(self.localSummaryFile):
                outtext = subprocess.run(['rclone', 'lsf', self.localMasterAnalysisDir.replace(self.localMasterDir,self.cloudMasterDir)], capture_output = True).stdout.decode().split('/\n')
                raise FileNotFoundError('Cant find '+ analysisID + '.csv.\nValid analysisIDs are: ' + ','.join(outtext))
            self.analysisID = analysisID
            self.readAnalysisFile()

        # Create filenames and parameters
        self.createFiles(projectID, modelID, analysisID)

    def createFiles(self, projectID, modelID, analysisID):
        self.localCredentialDir = self.localMasterDir + '__CredentialFiles/'
        self.localCredentialSpreadsheet = self.localCredentialDir + 'SAcredentials_1.json'
        self.localTankDir = self.localMasterDir + '__TankData/'

        self._createAnnotationData()
        if projectID is not None:
            self.setProjectID(projectID)
        if analysisID is not None:
            self._createMLData()
        if self.system == 'pi':
            self._createPiData()
        
        self._createParameters()

    def readAnalysisFile(self):
        self.downloadData(self.localSummaryFile)
        self.s_dt = pd.read_csv(self.localSummaryFile, index_col = 0, dtype={'Prep':bool,'Depth':bool,'Cluster': str})
        if 'DissectionTime' in self.s_dt:
            self.s_dt['DissectionTime'] = pd.to_datetime(self.s_dt.DissectionTime)
          
    def returnEmpty_s_dt(self, projectID = '', tankID = ''):
        data = {'RunAnalysis':False,'tankID':tankID,'StartingFiles':False,'Prep':False,'Depth':False,
                'Cluster':'VideoIndices: ','ManualAnnotation':0,'ClusterClassification':False,'Summary':False,
                'videoIDs':'VideoIndices: ','videoIDsToRun':'VideoIndices: ', 'videoIDsToAnnotate':'VideoIndices: ','Notes':''}
        my_index = pd.Index([projectID], name='projectID') 

        return pd.DataFrame(data, index = my_index)

    def setProjectID(self, projectID, print_issues = False):
        self.projectID = projectID
        self._createProjectData(projectID, print_issues = print_issues)
        if 'DissectionTime' in self.s_dt:
            self.dissectionTime = self.s_dt.loc[projectID]['DissectionTime']
        else:
            try:
                self.dissectionTime = self.lp.frames[-1].time
            except:
                pass

    def getProjectStates(self):
        # Dictionary to hold row of data
        row_data = {'tankID':'', 'StartingFiles':False, 'Prep':False, 'Depth':False, 'Cluster':'', 'ManualAnnotation':0, 'ClassifyClusters':False, 'Summary': False, 'videoIDs':''}

        #print('Checking project ' + self.projectID + ': ', end = '')
        try:
            self.downloadData(self.localLogfile)
            #self.downloadData(self.localLabeledClipsFile)
        except FileNotFoundError:
            print('Cant find logfile')
            return row_data
        self.lp = LP(self.localLogfile)
        row_data['videoIDs'] = 'VideoIndices: ' + ','.join([str(x) for x in range(len(self.lp.movies))])
        # Get all files on Dropbox
        allfiles = []
        for directory in [self.localProjectDir,self.localPrepDir,self.localVideoDir,self.localTroubleshootingDir,self.localAnalysisDir,self.localAllClipsDir, self.localManualLabelClipsDir, self.localManualLabelFramesDir, self.localSummaryDir]:
            outfiles = subprocess.run(['rclone','lsf',directory.replace(self.localMasterDir,self.cloudMasterDir)], capture_output = True).stdout.decode().split('\n')
            allfiles += [directory + x for x in outfiles]

        # List the files created by each analysis
        necessaryFiles = {}
        necessaryFiles['StartingFiles'] = [self.localLogfile, self.localPrepDir, self.localFrameTarredDir, self.localVideoDir, self.localFirstFrame, self.localLastFrame, self.localPiRGB, self.localFirstDepthRGB, self.localLastDepthRGB]
        necessaryFiles['Prep'] = [self.localDepthCropFile,self.localTransMFile,self.localVideoCropFile]
        necessaryFiles['Depth'] = [self.localSmoothDepthFile]
        necessaryFiles['Cluster'] = [self.localAllClipsDir, self.localManualLabelClipsDir, self.localManualLabelFramesDir]
        necessaryFiles['ManualAnnotation'] = [self.localLabeledClipsFile]
        necessaryFiles['ClassifyClusters'] = [self.localAllLabeledClustersFile]
        necessaryFiles['Summary'] = [self.localSummarizedClustersEvents, self.localSummarizedBuildingFigure, self.localSummarizedHourlyClusterFigure]
        
        for index,vid_obj in enumerate(self.lp.movies):
            vid_obj = self.returnVideoObject(index)
            necessaryFiles['StartingFiles'].append(vid_obj.localVideoFile)

        row_data['tankID'] = self.lp.tankID
        # Check if files exists

        directories = {}

        for analysis_type, analysis_files in necessaryFiles.items():
            row_data[analysis_type] = True
            for af in analysis_files:
                if af not in allfiles:
                    if '.mp4' in af:
                        if af.replace('.mp4','.h264') in allfiles:
                            print('Missing file: ' + af)
                            print('h264 file found')
                            continue
                    if analysis_type == 'StartingFiles':
                        print('Missing file: ' + af)
                    row_data[analysis_type] = False

        row_data['Cluster'] = 'VideoIndices: '
        # Get additional files necessary for analysis based on videos
        for index,vid_obj in enumerate(self.lp.movies):
            append = str(index) + ','
            vid_obj = self.returnVideoObject(index)
            if vid_obj.localLabeledClustersFile not in allfiles:
                append = ''
            if vid_obj.localAllClipsDir[:-1] + '.tar' not in allfiles:
                append = ''
            if vid_obj.localManualLabelClipsDir[:-1] + '.tar' not in allfiles:
                append = ''
            if vid_obj.localManualLabelFramesDir[:-1] + '.tar' not in allfiles:
                append = ''
            row_data['Cluster'] += append
        row_data['Cluster'] = row_data['Cluster'].rstrip(',')
        
        try:
            labeled_dt = pd.read_csv(self.localLabeledClipsFile, index_col = 'LID')
        
            labeled_dt['ProjectID'] = labeled_dt['ClipName'].str.split('__').str[0]
            row_data['ManualAnnotation'] = len(labeled_dt[labeled_dt.ProjectID == self.projectID])
        except FileNotFoundError:
            row_data['ManualAnnotation'] = 0

        remotefiles = self.getCloudFiles(self.localLabeledDLCClipsDir)
        if len([x for x in remotefiles if self.projectID in x]) == 3:
            row_data['DLCVideos'] = True
        else:
            row_data['DLCVideos'] = False

        return row_data

    def getProjectIDs(self, analysisType, projectIDs, clip_number = 0):
        bad_projects = []
        s_dt = self.s_dt

        if projectIDs is None:
            if analysisType == 'AnalyzeStates':
                projectIDs = s_dt[s_dt.RunAnalysis == True].index.to_list()
            elif analysisType == 'Prep':
                projectIDs = s_dt[(s_dt.StartingFiles == True) & (s_dt.RunAnalysis == True) & (s_dt[analysisType] == False)].index.to_list()
            elif analysisType == 'Depth':
                projectIDs = s_dt[(s_dt.Prep == True) & (s_dt.RunAnalysis == True) & (s_dt.Depth == False)].index.to_list()
            elif analysisType == 'Cluster':
                projectIDs = s_dt[(s_dt.Prep == True) & (s_dt.RunAnalysis == True)].index.to_list()
            elif analysisType == 'AnnotateVideos':
                projectIDs = s_dt[(s_dt.Cluster != 'VideoIndices: ') & (s_dt.RunAnalysis == True) & (s_dt.ManualAnnotation < clip_number)].index.to_list()
            elif analysisType == 'DLCVideos':
                projectIDs = s_dt[(s_dt.Cluster != 'VideoIndices: ') & (s_dt.RunAnalysis == True) & (s_dt.DLCVideos == False)].index.to_list()
            elif analysisType == 'TrainModel':
                projectIDs = []
            elif analysisType == 'ClassifyClusters':
                projectIDs = s_dt[(s_dt.Cluster != 'VideoIndices: ') & (s_dt.RunAnalysis == True) & (s_dt.ClassifyClusters == False)].index.to_list()
            elif analysisType == 'TrackFish':
                projectIDs = s_dt[(s_dt.RunAnalysis == True)].index.to_list()
            elif analysisType == 'PoseFish':
                projectIDs = s_dt[(s_dt.RunAnalysis == True)].index.to_list()
            elif analysisType == 'AssociateTracksWithClusters':
                projectIDs = s_dt[(s_dt.RunAnalysis == True) & (s_dt.TrackFish != 'VideoIndices: ') & (s_dt[analysisType] == False)].index.to_list()
            elif analysisType == 'Summary':
                projectIDs = s_dt[(s_dt.Depth == True) & (s_dt.ClassifyClusters == True) & (s_dt.AssociateTracksWithClusters == True) & (s_dt.RunAnalysis == True) & (s_dt[analysisType] == False)].index.to_list()
            elif analysisType == 'EditVideos':
                projectIDs = s_dt[(s_dt.Depth == True) & (s_dt.ClassifyClusters == True) & (s_dt.RunAnalysis == True) & (s_dt[analysisType] == False)].index.to_list()
            elif analysisType == 'FixAnnotations':
                projectIDs = s_dt[(s_dt.RunAnalysis == True) & (s_dt.ManualAnnotation > 0)].index.to_list()

        else:
            for projectID  in projectIDs:
                if projectID not in s_dt.index:
                    bad_projects.append(projectIDs)
        if len(bad_projects) > 0:
            raise Exception('Unknown ProjectIDs: ' + ','.join(bad_projects))
        return projectIDs

    def _createSubjectData(self):
        self.localSubjectDir = self.localMasterDir + '__ProjectData/' + self.analysisID + '/' + self.subjectID + '/'
        self.localSubjectDepthFile = self.localAnalysisDir + 'smoothedDepthData.npy'
        self.localSubjectDepthDataFrame = self.localAnalysisDir + 'smoothedDepthData.csv'

    def _createProjectData(self, projectID, print_issues = False):

        # Need information from AnalysisStates file to determine where project data is stored
        self.localProjectDir = self.localMasterDir + '__ProjectData/' + self.analysisID + '/' + projectID + '/'

        # Create logfile
        self.localLogfile = self.localProjectDir + 'Logfile.txt'
        self.localLogfileDir = self.localProjectDir + 'Logfiles/'
        self.localPrepLogfile = self.localLogfileDir + 'PrepLog.txt'
        self.localDepthLogfile = self.localLogfileDir + 'DepthLog.txt'
        # self.localClusterLogfile = self.localLogfileDir + 'ClusterLog.txt'
        self.localClusterClassificationLogfile = self.localLogfileDir + 'ClassifyLog.txt'
        
        # Data directories created by tracker
        self.localPrepDir = self.localProjectDir + 'PrepFiles/'
        self.localFrameDir = self.localProjectDir + 'Frames/'
        self.localFrameTarredDir = self.localProjectDir + 'Frames.tar'
        self.localVideoDir = self.localProjectDir + 'Videos/'
        self.localBackupDir = self.localProjectDir + 'Backups/'
        self.localFirstFrame = self.localPrepDir + 'FirstDepth.npy'
        self.localLastFrame = self.localPrepDir + 'LastDepth.npy'
        self.localPiRGB = self.localPrepDir + 'PiCameraRGB.jpg'
        self.localFirstDepthRGB = self.localPrepDir + 'FirstDepthRGB.jpg' 
        self.localLastDepthRGB = self.localPrepDir + 'LastDepthRGB.jpg'

        # Directories created by analysis
        self.localAnalysisDir = self.localProjectDir + 'MasterAnalysisFiles/'
        self.localSummaryDir = self.localProjectDir + 'Summary/'
        self.localEditVideosDir = self.localProjectDir + 'EditedVideos/'
        self.localAllClipsDir = self.localProjectDir + 'AllClips/'
        self.localManualLabelClipsDir = self.localProjectDir + 'MLClips/'
        self.localManualLabelFramesDir = self.localProjectDir + 'MLFrames/'
        self.localTroubleshootingDir = self.localProjectDir + 'Troubleshooting/'
        self.localTempDir = self.localProjectDir + 'Temp/'
        self.localPaceDir = self.localProjectDir + 'Pace/'
        self.localBuildPhotosDir = self.localProjectDir + 'TRImages/'
        # Files created by prep preparer
        self.localDepthCropFile = self.localAnalysisDir + 'DepthCrop.txt'
        self.localTransMFile = self.localAnalysisDir + 'TransMFile.npy'
        self.localVideoCropFile = self.localAnalysisDir + 'VideoCrop.txt'
        self.localPrepSummaryFigure = self.localSummaryDir + 'PrepSummary.pdf'
        self.localOldVideoCropFile = self.localAnalysisDir + 'VideoPoints.npy'
        self.logAnalysisFile = self.localSummaryDir + 'LogSummary.txt'
        self.trialOverviewFile = self.localSummaryDir + 'TrialSummary.pdf'

        # Files created by depth preparer
        self.localSmoothDepthFile = self.localAnalysisDir + 'smoothedDepthData.npy'
        self.localSmoothDepthDT = self.localAnalysisDir + 'smoothedDepthData.csv'
        self.localRGBDepthVideo = self.localAnalysisDir + 'DepthRGBVideo.mp4'
        self.localRawDepthFile = self.localTroubleshootingDir + 'rawDepthData.npy'
        self.localInterpDepthFile = self.localTroubleshootingDir + 'interpDepthData.npy'
        self.localDepthSummaryFile = self.localSummaryDir + 'DataSummary.csv'
        self.localDailyDepthSummaryFigure = self.localSummaryDir + 'DailyDepthSummary.pdf'
        self.localHourlyDepthSummaryFigure = self.localSummaryDir + 'HourlyDepthSummary.pdf'

        # Created by manual label video preparer
        self.localLabeledClipsProjectDir = self.localLabeledClipsDir + projectID + '/'

        # Files created by manual labelerer  preparers
        self.localNewLabeledFramesFile = self.localAnalysisDir + 'NewLabeledFrames.csv'
        self.localNewLabeledFramesDir = self.localTempDir + 'NewLabeledFrames/'

        # Files created by cluster classifier preparer
        self.localTempClassifierDir = self.localProjectDir + 'TempClassifier/'
        self.localAllLabeledClustersFile = self.localAnalysisDir + 'AllLabeledClusters.csv'

        # Files created by summary preparer
        self.localSummarizedClustersEvents = self.localSummaryDir + 'SummarizedClusterEvents.csv'
        self.localSummarizedBuildingFigure = self.localSummaryDir + 'SummarizedIntegratedBuilding.pdf'
        self.localSummarizedHourlyClusterFigure = self.localSummaryDir + 'SummarizedHourlyClusterEvents.pdf'
        self.localSummarizedHistogramFigure = self.localSummaryDir + 'SummarizedHistograms.pdf'

        # Files created by fish_tracking preparer
        self.localAllFishTracksFile = self.localAnalysisDir + 'AllTrackedFish.csv'
        self.localAllTracksSummaryFile = self.localAnalysisDir + 'AllSummarizedTracks.csv'
        

        try:
            self.downloadData(self.localLogfile)
            if print_issues:
                self.createDirectory(self.localSummaryDir)
                self.lp = LP(self.localLogfile, print_issues = self.logAnalysisFile)
            else:
                try:
                    self.lp = LP(self.localLogfile)
                except:
                    print('Issue with Logfile')
        except FileNotFoundError:
            #print('No logfile created yet for ' + projectID)
            pass 

    def _createMLData(self):

        self.localMLDir = self.localMasterDir + '__MachineLearningModels/'
        self.localYOLODir = self.localMLDir + 'YOLOModels/'
        self.localMLPoseDir = self.localMLDir + 'PoseDir/'

        self.localYOLOModelDir = self.localYOLODir + self.analysisID + '/'
        self.localYOLOModelFile = self.localYOLOModelDir + '/weights/best.pt'

        self.localPoseModelDir = self.localMLPoseDir + self.analysisID + '/'
        self.localPoseModelFile = self.localPoseModelDir + '/weights/best.pt'

        self.local3DModelDir = self.localMLDir + 'VideoModels/' + self.analysisID + '/'
        self.local3DModelTempDir = self.local3DModelDir + 'Temp/'

        self.localVideoProjectsFile = self.local3DModelDir + 'videoToProject.csv'

        self.localModelDataSummary = self.local3DModelDir + 'DataSummaryByAnalysisID.csv'
        self.localModelCondaVersionsFile = self.local3DModelDir + 'CondaVersions.log'
        self.localModelCommandsFile = self.local3DModelDir + 'commands.log'
        self.localModelDataBreakdown = self.local3DModelDir + 'dataBreakdown.json'

        self.localVideoModelFile = self.local3DModelDir + 'model.pth'
        self.localModelConfusionFile = self.local3DModelDir + 'confusionMatrix.csv'
        self.localModelProjectAccuracy = self.local3DModelDir + 'projectAccuracy.csv'

    def _createPiData(self):
        self.localCredentialDir = self.localMasterDir + '__CredentialFiles/'
        self.localCredentialSpreadsheet = self.localCredentialDir + 'SAcredentials_1.json'
        self.localCredentialDrive = self.localCredentialDir +  'DriveCredentials.txt'
        self.localEmailCredentialFile = self.localCredentialDir + 'iof_credentials/sendgrid_key.secret'

    def _createAnnotationData(self):
        self.localAnnotationDir = self.localMasterDir + '__AnnotatedData/'
        self.local3DVideosDir = self.localAnnotationDir + 'LabeledVideos/'
        self.localObjectDetectionDir = self.localAnnotationDir + 'ObjectDetection/'
        self.localPoseDir = self.localAnnotationDir + 'PoseData/'

        self.localLabeledClipsFile = self.local3DVideosDir + 'ManualLabels.csv'
        self.localLabeledClipsDir = self.local3DVideosDir + 'Clips/'

        self.localYOLOAnnotationDir = self.localObjectDetectionDir + 'YOLO_Annotations/'
        self.localLabeledDLCClipsDir = self.localObjectDetectionDir + 'DLCClips/'

    def returnVideoObject(self, index):
        self._createParameters()

        try:
            videoObj = self.lp.movies[index]
        except AttributeError:
            pdb.set_trace()

        videoObj.localVideoFile = self.localProjectDir + videoObj.mp4_file
        videoObj.localh264File = self.localProjectDir + videoObj.h264_file
        videoObj.localDLCVideoFile = self.lp.projectID + '__' + videoObj.baseName + '__' + 'DLC.mp4'
        videoObj.localHMMFile = self.localTroubleshootingDir + videoObj.baseName + '.hmm'
        videoObj.localRawCoordsFile = self.localTroubleshootingDir + videoObj.baseName + '_rawCoords.npy'
        videoObj.localLabeledCoordsFile = self.localTroubleshootingDir + videoObj.baseName + '_labeledCoords.npy'
        videoObj.localLabeledClustersFile = self.localTroubleshootingDir + videoObj.baseName + '_labeledClusters.csv'
        videoObj.localFishDetectionsFile = self.localTroubleshootingDir + videoObj.baseName + '_fishDetections.csv'
        videoObj.localFishTracksFile = self.localTroubleshootingDir + videoObj.baseName + '_fishTracks.csv'
        videoObj.localFishSexFile = self.localTroubleshootingDir + videoObj.baseName + '_fishSex.csv'
        videoObj.localFishPoseFile = self.localTroubleshootingDir + videoObj.baseName + '_fishPose.csv'

        videoObj.localAllClipsDir = self.localAllClipsDir + videoObj.baseName + '/'
        videoObj.localManualLabelClipsDir = self.localManualLabelClipsDir + videoObj.baseName + '/'
        videoObj.localManualLabelFramesDir = self.localManualLabelFramesDir + videoObj.baseName + '/'
        videoObj.localAllClipsPrefix = self.localAllClipsDir + self.lp.projectID + '_' + videoObj.baseName
        videoObj.localManualLabelClipsPrefix = self.localManualLabelClipsDir + self.lp.projectID + '_' + videoObj.baseName
        videoObj.localIntensityFile = self.localSummaryDir + videoObj.baseName + '_intensity.pdf'
        videoObj.localTempDir = self.localTempDir + videoObj.baseName + '/'
        videoObj.localLogfile = self.localLogfileDir + 'ClusterLog_' + str(index) + '.txt'
        videoObj.localClassifyLogfile = self.localLogfileDir + 'ClassifyLog_' + str(index) + '.txt'
        videoObj.localYOLOLogfile = self.localLogfileDir + 'YOLOLog_' + str(index) + '.txt'

        self.createDirectory(videoObj.localTempDir)

        return videoObj

    def _createParameters(self):

        # Depth related parameters
        self.hourlyDepthThreshold = 0.3  # cm
        self.dailyDepthThreshold = 1  # cm
        self.totalDepthThreshold = 1.0  # cm

        # Cluster related parameters
        self.hourlyClusterThreshold = 0.6  # events/cm^2
        self.dailyClusterThreshold = 1.2  # events/cm^2
        self.totalClusterThreshold = 3.0  # events/cm^2

        # Parameters related to both depth and cluster analysis
        self.hourlyMinPixels = 100
        self.dailyMinPixels = 100
        self.totalMinPixels = 100
        self.pixelLength = 0.1030168618  # cm / pixel
        self.bowerIndexFraction = 0.1

        # Video related parameters
        self.lightsOnTime = 8
        self.lightsOffTime = 18

        # DB Scan related parameters
        self.minMagnitude = 0
        self.treeR = 22 
        self.leafNum = 190 
        self.neighborR = 22
        self.timeScale = 10
        self.eps = 18
        self.minPts = 90 
        self.delta = 1.0 # Batches to calculate clusters

        # Clip creation parameters
        self.nManualLabelClips = 1200
        self.delta_xy = 100
        self.delta_t = 60
        self.smallLimit = 500

        # Manual Label Frame 
        self.nManualLabelFrames = 500

    def _identifyMasterDirectory(self, mounted_dir = '/media/pi/'):
        
        if platform.node() == 'raspberrypi' or 'Pi' in platform.node() or 'bt-' in platform.node() or 'sv-' in platform.node():
            writableDirs = []
            try:
                possibleDirs = os.listdir(mounted_dir)
            except FileNotFoundError:
                return

            for d in possibleDirs:

                try:
                    with open(mounted_dir + d + '/temp.txt', 'w') as f:
                        print('Test', file = f)
                    with open(mounted_dir + d + '/temp.txt', 'r') as f:
                        for line in f:
                            if 'Test' in line:
                                writableDirs.append(d)
                except:
                    pass
                try:
                    os.remove(mounted_dir + d + '/temp.txt')
                except FileNotFoundError:
                    continue
                except OSError:
                    continue
            
            if len(writableDirs) == 1:
                self.localMasterDir = mounted_dir + d + '/CichlidAnalyzer/'
                self.system = 'pi'
            elif len(writableDirs) == 0:
                raise Exception('No writable drives in /media/pi/')
            else:
                raise Exception('Multiple writable drives in /media/pi/. Options are: ' + str(writableDirs))
        elif platform.node() == 'ebb-utaka.biosci.gatech.edu':
            self.localMasterDir = '/mnt/Storage/' + os.getenv('USER') + '/Temp/CichlidAnalyzer/'
            self.system = 'utaka'

        else:
            self.localMasterDir = os.getenv('HOME').rstrip('/') + '/' + 'Temp/CichlidAnalyzer/'
            self.system = 'other'

    def createDirectory(self, directory):
        if not os.path.exists(directory):
            os.makedirs(directory)

    def downloadData(self, local_data, tarred = False, tarred_subdirs = False, allow_errors=False, quiet=False):
        if local_data is None:
            return
        relative_name = local_data.rstrip('/').split('/')[-1] + '.tar' if tarred else local_data.rstrip('/').split('/')[-1]
        local_path = local_data.split(local_data.rstrip('/').split('/')[-1])[0]
        cloud_path = local_path.replace(self.localMasterDir, self.cloudMasterDir)
        

        # print("Relative name:",relative_name)
        # print("local_path:",local_path)
        # print("cloud_path ",cloud_path)
        cloud_objects = subprocess.run(['rclone', 'lsf', cloud_path], capture_output = True, encoding = 'utf-8').stdout.split()
        # pdb.set_trace()


        if relative_name + '/' in cloud_objects: #directory
            output = subprocess.run(['rclone', 'copy', cloud_path + relative_name, local_path + relative_name], capture_output = True, encoding = 'utf-8')
            if output.returncode != 0:
                pdb.set_trace()
        elif relative_name in cloud_objects: #file
            output = subprocess.run(['rclone', 'copy', cloud_path + relative_name, local_path], capture_output = True, encoding = 'utf-8')
            if output.returncode != 0:
                pdb.set_trace()
        else:
            if allow_errors:
                if not quiet:
                    print('Warning: Cannot find {}. Continuing'.format(cloud_path + relative_name))
                else:
                    pass
            else:
                #pdb.set_trace()
                raise FileNotFoundError('Cant find file for download: ' + cloud_path + relative_name)
        # pdb.set_trace()
        if not os.path.exists(local_path + relative_name):
            if allow_errors:
                if not quiet:
                    print('Warning. Cannot download {}. Continuing'.format(local_path + relative_name))
                else:
                    pass
            else:
                raise FileNotFoundError('Error downloading: ' + cloud_path + relative_name)

        if tarred:
            # Untar directory
            output = subprocess.run(['tar', '-xvf', local_path + relative_name, '-C', local_path], capture_output = True, encoding = 'utf-8')
            output = subprocess.run(['rm', '-f', local_path + relative_name], capture_output = True, encoding = 'utf-8')

        if tarred_subdirs:
            for d in [x for x in os.listdir(local_data) if '.tar' in x]:
                output = subprocess.run(['tar', '-xvf', local_data + d, '-C', local_data, '--strip-components', '1'], capture_output = True, encoding = 'utf-8')
                os.remove(local_data + d)

    def uploadData(self, local_data, tarred = False):

        attempt = 1
        while True:
            relative_name = local_data.rstrip('/').split('/')[-1]
            local_path = local_data.split(relative_name)[0]
            cloud_path = local_path.replace(self.localMasterDir, self.cloudMasterDir)

            if tarred:
                output = subprocess.run(['tar', '-cvf', local_path + relative_name + '.tar', '-C', local_path, relative_name], capture_output = True, encoding = 'utf-8')
                if output.returncode != 0:
                    print(output.stderr)
                    if attempt < 3:
                        attempt += 1
                        continue
                    raise Exception('Error in tarring ' + local_data)
                relative_name += '.tar'

            if os.path.isdir(local_path + relative_name):
                # pdb.set_trace()
                output = subprocess.run(['rclone', 'copy', local_path + relative_name, cloud_path + relative_name], capture_output = True, encoding = 'utf-8')
                #subprocess.run(['rclone', 'check', local_path + relative_name, cloud_path + relative_name], check = True) #Troubleshooting directory will have depth data in it when you upload the cluster data

            elif os.path.isfile(local_path + relative_name):
                # pdb.set_trace()
                #print(['rclone', 'copy', local_path + relative_name, cloud_path])
                output = subprocess.run(['rclone', 'copy', local_path + relative_name, cloud_path], capture_output = True, encoding = 'utf-8')
                #output = subprocess.run(['rclone', 'check', local_path + relative_name, cloud_path], check = True, capture_output = True, encoding = 'utf-8')
            else:
                raise Exception(local_data + ' does not exist for upload')

            if output.returncode != 0:
                if attempt < 3:
                    attempt += 1
                    continue
                raise Exception('Error in uploading file: ' + output.stderr)
            else:
                return

    def uploadAndMerge(self, local_data, master_file, tarred = False, ID = False):
        if os.path.isfile(local_data):
            #We are merging two crv files
            self.downloadData(master_file)
            import pandas as pd
            if ID:
                old_dt = pd.read_csv(master_file, index_col = ID)
                new_dt = pd.read_csv(local_data, index_col = ID)
                old_dt = old_dt.append(new_dt)
                old_dt.index.name = ID
            else:
                old_dt = pd.read_csv(master_file)
                new_dt = pd.read_csv(local_data)
                old_dt = old_dt.append(new_dt)
            
            old_dt.to_csv(master_file, sep = ',')
            self.uploadData(master_file)
        else:
            #We are merging two tarred directories
            try:        
                self.downloadData(master_file, tarred = True)
            except FileNotFoundError:
                self.createDirectory(master_file)
            for nfile in os.listdir(local_data):
                subprocess.run(['mv', local_data + nfile, master_file])
            self.uploadData(master_file, tarred = True)

    def checkFileExists(self, local_data, tarred = False):
        relative_name = local_data.rstrip('/').split('/')[-1]
        local_path = local_data.split(relative_name)[0]
        cloud_path = local_path.replace(self.localMasterDir, self.cloudMasterDir)
        if tarred:
            relative_name += '.tar'
        output = subprocess.run(['rclone', 'lsf', cloud_path], capture_output = True, encoding = 'utf-8')
        remotefiles = [x.rstrip('/') for x in output.stdout.split('\n')]
        # pdb.set_t race()
        if relative_name in remotefiles:
            return True
        else:
            return False

    def getCloudFiles(self, local_data):
        local_path = local_data.rstrip('/')
        cloud_path = local_path.replace(self.localMasterDir, self.cloudMasterDir)
        output = subprocess.run(['rclone', 'lsf', cloud_path], capture_output = True, encoding = 'utf-8')
        remotefiles = [x.rstrip('/') for x in output.stdout.split('\n')]
        return remotefiles

    def deleteCloudData(self, local_data):
        if self.checkFileExists(local_data):
            cloud_path = local_data.replace(self.localMasterDir, self.cloudMasterDir)
            output = subprocess.run(['rclone', 'purge', cloud_path], capture_output = True, encoding = 'utf-8')
            if self.checkFileExists(local_data):
                pdb.set_trace()
            else:
                return

