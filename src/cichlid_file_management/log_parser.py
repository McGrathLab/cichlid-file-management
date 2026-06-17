
import os, sys, io, pdb
import datetime as dt

#add delta value for frame and background
#make masterstart return 2 lines

class LogFormatError(Exception):
    pass

class LogParser:    
    def __init__(self, logfile, running = False, print_issues = False):

        
        self.logfile = logfile
        self.master_directory = logfile.replace(logfile.split('/')[-1], '') + '/'
        self.running = running
        self.malformed_file = []

        self.parse_log()
        self.check_malformed(print_issues = print_issues)
        self.height = 480 # This is a temporary fix to hardcode in these values
        self.width = 640

    def parse_log(self):
        self.speeds = []
        self.frames = []
        self.movies = []
        self.tankresetstart = []
        self.tankresetstop = []
        self.restarts = []
        
        with open(self.logfile) as f:
            for line in f:
                line = line.rstrip()
                info_type = line.split(':')[0]
                if info_type == 'MasterStart':
                    try:
                        self.system
                        self.device
                        self.camera
                        self.uname
                        self.tankID
                        self.projectID
                        self.analysisID
                    except AttributeError:
                        try:
                            self.system, self.device, self.camera, self.uname, self.tankID, self.projectID, self.analysisID, self.sampleID = self._ret_data(line, ['System', 'Device', 'Camera','Uname', 'TankID', 'ProjectID', 'AnalysisID','SampleID'])
                        except:
                            self.system, self.device, self.camera, self.uname, self.tankID, self.projectID, self.analysisID = self._ret_data(line, ['System', 'Device', 'Camera','Uname', 'TankID', 'ProjectID', 'AnalysisID'])
                            self.sampleID = None
                    else:
                        self.malformed_file.append('MasterStart is present more than once in the Logfile')

                if info_type == 'MasterRecordInitialStart':
                    self.master_start = self._ret_data(line, ['Time'])[0]
                    
                if info_type == 'DiagnoseSpeed':
                    self.speeds.append(self._ret_data(line, 'Rate'))
                    
                if info_type == 'FrameCaptured':
                    t_list = self._ret_data(line, ['NpyFile','PicFile','Time','AvgMed','AvgStd','GP','LOF'])
                    self.frames.append(FrameObj(*t_list))
                    
                if info_type == 'PiCameraStarted':
                    t_list = self._ret_data(line,['Time','VideoFile', 'PicFile', 'FrameRate', 'Resolution'])
                    self.movies.append(MovieObj(*t_list))

                if info_type == 'PiCameraStopped':
                    t_list = self._ret_data(line, ['Time', 'File'])
                    try:
                        [x for x in self.movies if x.h264_file == t_list[1]][0].endTime = t_list[0]
                    except IndexError:
                        self.malformed_file.append('Cant find PiCameraStart for ' + t_list[1])
                
                if info_type == 'TankResetStart':
                    t_list = self._ret_data(line, ['Time'])
                    self.tankresetstart.append(t_list[0])
                
                if info_type == 'TankResetStop':
                    t_list = self._ret_data(line, ['Time'])
                    self.tankresetstop.append(t_list[0])

                if info_type == 'MasterRecordStop': 
                    self.master_stop = self._ret_data(line, ['Time'])[0]

                if info_type == 'MasterRecordRestart':
                    self.restarts.append(self._ret_data(line, ['Time'])[0])

        self.frames.sort(key = lambda x: x.time)
        if self.running:
            self.movies[-1].endTime = self.frames[-1].time
        #for movie in self.movies:
        #    if movie.endTime == '':
        #        print('Warning: No end time for ' + movie.mp4_file)
        # Create trials
        if self.running:
            end_time = dt.datetime.now()
            try:
                self.num_trials = len(self.tankresetstop) + 1
                self.trials = [Trial(self.master_start, self.tankresetstart[0], self.tankresetstop[0], self.frames, self.movies, self.sampleID)]
                for j in range(self.num_trials-2):
                    self.trials.append(Trial(self.tankresetstop[j], self.tankresetstart[j+1], self.tankresetstop[j+1], self.frames, self.movies, self.sampleID))
                self.trials.append(Trial(self.tankresetstop[-1], end_time, None, self.frames, self.movies, self.sampleID))
            except IndexError:
                self.trials = [Trial(self.master_start, end_time, None, self.frames, self.movies, self.sampleID)]
        else:
            self.num_trials = len(self.tankresetstop)
            self.trials = [Trial(self.master_start, self.tankresetstart[0], self.tankresetstop[0], self.frames, self.movies, self.sampleID)]
            for j in range(self.num_trials - 1):
                self.trials.append(Trial(self.tankresetstop[j], self.tankresetstart[j+1], self.tankresetstop[j+1], self.frames, self.movies, self.sampleID))

        daylight_frames = [x for x in self.frames if x.lof == True]
        for frame in self.frames:
            if not frame.lof:
                if frame.time > daylight_frames[-1].time:
                    frame.nearest_day = (daylight_frames[-1],daylight_frames[-1])
                elif frame.time < daylight_frames[0].time:
                    frame.nearest_day = (daylight_frames[0],daylight_frames[0])


        self.lastFrameCounter=len(self.frames)
        self.lastVideoCounter=len(self.movies)
    
    def check_malformed(self, print_issues = False):
        trial_issues = False
        try:
            self.master_start
        except:
            self.malformed_file.append('No master start information')
        try:
            self.master_stop
        except:
            self.malformed_file.append('No master stop information')
            self.master_stop = self.frames[-1].time

        for movie in self.movies:
            if movie.endTime == '':
                self.malformed_file.append('No end time information for: ' + movie.h264_file)
                movie.endTime = movie.startTime.replace(hour = 18, minute = 0)

        try:
            self.tankresetstart
            self.tankresetstop
        except:
            self.malformed_file.append('No TankResetStartStopInformation')
            trial_issues = True
        else:
            if len(self.tankresetstart) != len(self.tankresetstop):
                self.malformed_file.append('# of TankResetStarts != # of TankResetStops')
                trial_issues = True

            else:
                for start,stop in zip(self.tankresetstart,self.tankresetstop):
                    if stop - start > dt.timedelta(hours = 4) or stop <= start:
                        self.malformed_file.append('TimeDelta Unusual for tankresetstart and stop')
                        #trial_issues = True

        if len(self.restarts) > 0:
            self.malformed_file.append('# of restarts: ' + str(len(self.restarts)))

        for i,trial in enumerate(self.trials):
            trial.figureFile = 'Trial_' + str(i+1) + '_SummaryFigure.pdf'
            trial.number = i+1
            for day_start,day_stop in trial.days:
                expected_frames = int((day_stop.time - day_start.time).total_seconds()/60/5)
                if expected_frames == 0:
                    continue
                actual_frames = len([x for x in self.frames if x.time >= day_start.time and x.time <= day_stop.time])
                if actual_frames/expected_frames < .75:
                    self.malformed_file.append('Missing frames > 25% on day: ' + str(day_start.time.date()))

        if type(print_issues) == str or print_issues is True:
            if type(print_issues) == str:
                with open(print_issues,'w') as f:
                    for mf in self.malformed_file:
                        print(mf,file = f)
            for mf in self.malformed_file:
                print(mf)
        
            if trial_issues:
                raise Exception

    def _ret_data(self, line, data):
        out_data = []
        if type(data) != list:
            data = [data]
        for d in data:
            try:
                t_data = line.split(d + ': ')[1].split(',,')[0]
            except IndexError:
                try:
                    t_data = line.split(d + ':')[1].split(',,')[0]
                except IndexError:
                    try:
                        t_data = line.split(d + '=')[1].split(',,')[0]
                    except IndexError:
                        out_data.append('Error')
                        continue
            # Is it a date?
            try:
                out_data.append(dt.datetime.strptime(t_data, '%Y-%m-%d %H:%M:%S.%f'))
                continue
            except ValueError:
                pass
            try:
                out_data.append(dt.datetime.strptime(t_data, '%Y-%m-%d %H:%M:%S'))
                continue
            except ValueError:
                pass

            try:
                out_data.append(dt.datetime.strptime(t_data, '%a %b %d %H:%M:%S %Y'))
                continue
            except ValueError:
                pass
            try:
                out_data.append(dt.datetime.strptime(t_data, '%H:%M:%S'))
                continue
            except ValueError:
                pass
            # Is it a tuple?
            if t_data[0] == '(' and t_data[-1] == ')':
                out_data.append(tuple(int(x) for x in t_data[1:-1].split(', ')))
                continue
            # Is it a boolean?
            if t_data == 'True': 
                out_data.append(True)
                continue
            if t_data == 'False':
                out_data.append(False)
                continue 
            # Is it an int?
            try:
                out_data.append(int(t_data))
                continue
            except ValueError:
                pass
            # Is it a float?
            try:
                out_data.append(float(t_data))
                continue
            except ValueError:
                pass
            # Is it a resolution (e.g. 1296x972)
            try:
                out_data.append((int(t_data.split('x')[0]), int(t_data.split('x')[1])))
            except ValueError:
                # Keep it as a string
                out_data.append(t_data)
        return out_data

class FrameObj:
    def __init__(self, npy_file, pic_file, time, med, std, gp, lof):
        self.npy_file = npy_file
        self.pic_file = pic_file
        self.std_file = npy_file.replace('Frame_', 'Frame_std_')
        self.time = time
        self.med = med
        self.std = std
        self.gp = gp
        self.lof = lof
        self.rel_day = 0
        self.frameDir = npy_file.replace(npy_file.split('/')[-1],'')
        self.index = int(npy_file.split('_')[1].split('.npy')[0]) - 1
 
class MovieObj:
    def __init__(self, time, movie_file, pic_file, framerate, resolution):
        self.startTime = time
        self.endTime = ''
        if '.mp4' in movie_file:
            self.mp4_file = movie_file
            self.h264_file =  movie_file.replace('.mp4', '') + '.h264'
        else:
            self.h264_file =  movie_file
            self.mp4_file =  movie_file.replace('.h264', '') + '.mp4'
        self.pic_file =  pic_file
        self.framerate = framerate
        self.movieDir = movie_file.replace(movie_file.split('/')[-1],'')
        self.baseName = self.mp4_file.split('/')[-1].replace('.mp4', '')
        self.height = resolution[1]
        self.width = resolution[0]
        self.index = int(movie_file.split('_vid')[0].split('/')[-1]) - 1

class Trial:
    def __init__(self, start_time, stop_time, reset_time, all_frames, all_movies, sampleID):
        self.startTime = start_time
        self.stopTime = stop_time
        self.resetTime = reset_time
        self.sampleID = sampleID
        #self.tempName = sampleID.split('-')[1] + '_TR_' + str(reset_time.month) + '.' + str(reset_time.day) + '.' + str(reset_time.year)[-2:] +'.jpeg'
        if reset_time is not None:
            try:
                self.reset_frame = [x for x in all_frames if x.time > reset_time][0]
            except TypeError:
                pdb.set_trace()
        try:
            self.frames = [x for x in all_frames if x.time > start_time and x.time < stop_time]
            self.daylight_frames = [x for x in self.frames if x.lof == True]
        except IndexError:
            pdb.set_trace()
        if reset_time is not None:
            try:
                self.reset_frame = [x for x in all_frames if x.time > reset_time][0]
            except IndexError:
                pdb.set_trace()
        try:
            self.movies = [x for x in all_movies if x.endTime > start_time and x.startTime < stop_time]
        except:
            for movie in all_movies:
                if movie.endTime == '':
                    movie.endTime = movie.startTime.replace(hour = 18, minute = 0)
            self.movies = [x for x in all_movies if x.endTime > start_time and x.startTime < stop_time]

        days = {}

        days = {}
        for frame in self.daylight_frames:
            frame.rel_day = (frame.time - all_frames[-1].time.replace(hour = 0, minute = 0)).days
            try:
                days[frame.time.date()] = (days[frame.time.date()][0],frame)
            except KeyError:
                days[frame.time.date()] = (frame,frame)
            except IndexError:
                pdb.set_trace()
        try:
            self.days = [x for x in days.values()]        
        except IndexError:
            pdb.set_trace()
        self.days_videos = []
        for start,stop in self.days:
            movie_idxs = [x.index for x in all_movies if x.endTime > start.time and x.startTime < stop.time]
            self.days_videos.append(','.join([str(x) for x in movie_idxs]))
        self.num_days = len(self.days)
        self.num_rows = int((self.num_days - 1) / 6) + 1 # Also a row for the top
        for frame in self.frames:
            if not frame.lof:
                try:
                    day_first = [x[1] for x in self.days if x[1].time < frame.time][-1]
                    day_second = [x[0] for x in self.days if x[0].time > frame.time][0]
                except IndexError:
                    continue
                frame.nearest_day = (day_first,day_second)