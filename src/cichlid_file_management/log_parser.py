
"""Parse a tracker run logfile into structured frame / movie / trial objects.

The tracker writes one tagged line per event (MasterStart, FrameCaptured,
PiCameraStarted/Stopped, TankResetStart/Stop, ...). This module turns that log
back into the objects the analysis pipeline and the upload step consume. It is
the read side of a contract with cichlid_tracker's `_print` calls -- the field
names here must match the field names written there.
"""

import datetime as dt


class LogFormatError(Exception):
    pass


class LogParser:
    # Log formats this parser understands. A logfile records its format with a
    # `LogVersion: N` line; logs without one (e.g. the legacy h264-era logs) are
    # treated as version 0. Unsupported versions fail fast with a clear message
    # instead of misparsing -- to support a new format, add its number here and
    # branch on self.version wherever the parsing differs.
    SUPPORTED_VERSIONS = (1,)

    def __init__(self, logfile, running=False, print_issues=False):
        self.logfile = logfile
        self.master_directory = logfile.replace(logfile.split("/")[-1], "") + "/"
        self.running = running
        self.malformed_file = []

        # Hardcoded depth dimensions (match the RealSense stream config).
        self.height = 480
        self.width = 640

        self.version = self._detect_version()
        if self.version not in self.SUPPORTED_VERSIONS:
            raise LogFormatError(
                f"Logfile version {self.version} is not supported by this LogParser "
                f"(supported: {self.SUPPORTED_VERSIONS}). It was likely written by a "
                f"different tracker version.")

        self.parse_log()
        self.check_malformed(print_issues=print_issues)

    def _detect_version(self):
        """Return the logfile's format version. A missing LogVersion marker means
        a legacy (pre-versioning) log, reported as version 0."""
        with open(self.logfile) as f:
            for line in f:
                if line.startswith("LogVersion:"):
                    return self._ret_data(line.rstrip(), ["LogVersion"])[0]
        return 0

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
                info_type = line.split(":")[0]

                if info_type == "MasterStart":
                    if hasattr(self, "system"):
                        self.malformed_file.append(
                            "MasterStart is present more than once in the logfile")
                    else:
                        self._parse_master_start(line)

                elif info_type == "MasterRecordInitialStart":
                    self.master_start = self._ret_data(line, ["Time"])[0]

                elif info_type == "DiagnoseSpeed":
                    self.speeds.append(self._ret_data(line, "Rate"))

                elif info_type == "FrameCaptured":
                    t_list = self._ret_data(
                        line, ["NpyFile", "PicFile", "Time", "AvgMed", "AvgStd", "GP", "LOF"])
                    self.frames.append(FrameObj(*t_list))

                elif info_type == "PiCameraStarted":
                    t_list = self._ret_data(
                        line, ["Time", "VideoFile", "PicFile", "FrameRate", "Resolution"])
                    self.movies.append(MovieObj(*t_list))

                elif info_type == "PiCameraStopped":
                    t_list = self._ret_data(line, ["Time", "File"])
                    matches = [x for x in self.movies if x.mp4_file == t_list[1]]
                    if matches:
                        matches[0].endTime = t_list[0]
                    else:
                        self.malformed_file.append(
                            "Can't find PiCameraStart for " + str(t_list[1]))

                elif info_type == "TankResetStart":
                    self.tankresetstart.append(self._ret_data(line, ["Time"])[0])

                elif info_type == "TankResetStop":
                    self.tankresetstop.append(self._ret_data(line, ["Time"])[0])

                elif info_type == "MasterRecordStop":
                    self.master_stop = self._ret_data(line, ["Time"])[0]

                elif info_type == "MasterRecordRestart":
                    self.restarts.append(self._ret_data(line, ["Time"])[0])

        self.frames.sort(key=lambda x: x.time)
        if self.running and self.movies and self.frames:
            self.movies[-1].endTime = self.frames[-1].time

        self._build_trials()

        daylight_frames = [x for x in self.frames if x.lof is True]
        if daylight_frames:
            for frame in self.frames:
                if not frame.lof:
                    if frame.time > daylight_frames[-1].time:
                        frame.nearest_day = (daylight_frames[-1], daylight_frames[-1])
                    elif frame.time < daylight_frames[0].time:
                        frame.nearest_day = (daylight_frames[0], daylight_frames[0])

        self.lastFrameCounter = len(self.frames)
        self.lastVideoCounter = len(self.movies)

    def _parse_master_start(self, line):
        fields = ["System", "Device", "Camera", "Uname",
                  "TankID", "ProjectID", "AnalysisID", "SampleID"]
        vals = self._ret_data(line, fields)
        (self.system, self.device, self.camera, self.uname, self.tankID,
         self.projectID, self.analysisID) = vals[:7]
        # SampleID is optional; a missing key comes back as the "Error" sentinel.
        self.sampleID = vals[7] if vals[7] != "Error" else None

    def _build_trials(self):
        """Segment the run into Trials at each tank-reset stop. Robust to runs
        with no resets (one trial) and to a running log (open final trial)."""
        sampleID = getattr(self, "sampleID", None)
        start = getattr(self, "master_start", None)

        if self.running:
            end_time = dt.datetime.now()
            try:
                self.num_trials = len(self.tankresetstop) + 1
                self.trials = [Trial(start, self.tankresetstart[0],
                                     self.tankresetstop[0], self.frames, self.movies, sampleID)]
                for j in range(self.num_trials - 2):
                    self.trials.append(Trial(
                        self.tankresetstop[j], self.tankresetstart[j + 1],
                        self.tankresetstop[j + 1], self.frames, self.movies, sampleID))
                self.trials.append(Trial(
                    self.tankresetstop[-1], end_time, None, self.frames, self.movies, sampleID))
            except IndexError:
                self.trials = [Trial(start, end_time, None, self.frames, self.movies, sampleID)]
            return

        # Completed log.
        self.num_trials = len(self.tankresetstop)
        if self.num_trials == 0:
            stop = getattr(self, "master_stop", None)
            if stop is None:
                stop = self.frames[-1].time if self.frames else dt.datetime.now()
            self.trials = [Trial(start, stop, None, self.frames, self.movies, sampleID)]
            return

        self.trials = [Trial(start, self.tankresetstart[0], self.tankresetstop[0],
                             self.frames, self.movies, sampleID)]
        for j in range(self.num_trials - 1):
            self.trials.append(Trial(
                self.tankresetstop[j], self.tankresetstart[j + 1],
                self.tankresetstop[j + 1], self.frames, self.movies, sampleID))

    def check_malformed(self, print_issues=False):
        trial_issues = False
        if not hasattr(self, "master_start"):
            self.malformed_file.append("No master start information")
        if not hasattr(self, "master_stop"):
            self.malformed_file.append("No master stop information")
            self.master_stop = self.frames[-1].time if self.frames else None

        for movie in self.movies:
            if movie.endTime == "":
                self.malformed_file.append("No end time information for: " + movie.mp4_file)
                movie.endTime = movie.startTime.replace(hour=18, minute=0)

        if len(self.tankresetstart) != len(self.tankresetstop):
            self.malformed_file.append("# of TankResetStarts != # of TankResetStops")
            trial_issues = True
        else:
            for start, stop in zip(self.tankresetstart, self.tankresetstop):
                if stop - start > dt.timedelta(hours=4) or stop <= start:
                    self.malformed_file.append(
                        "TimeDelta unusual for tankresetstart and stop")

        if len(self.restarts) > 0:
            self.malformed_file.append("# of restarts: " + str(len(self.restarts)))

        for i, trial in enumerate(self.trials):
            trial.figureFile = "Trial_" + str(i + 1) + "_SummaryFigure.pdf"
            trial.number = i + 1
            for day_start, day_stop in trial.days:
                expected_frames = int((day_stop.time - day_start.time).total_seconds() / 60 / 5)
                if expected_frames == 0:
                    continue
                actual_frames = len([x for x in self.frames
                                     if day_start.time <= x.time <= day_stop.time])
                if actual_frames / expected_frames < 0.75:
                    self.malformed_file.append(
                        "Missing frames > 25% on day: " + str(day_start.time.date()))

        if isinstance(print_issues, str) or print_issues is True:
            if isinstance(print_issues, str):
                with open(print_issues, "w") as f:
                    for mf in self.malformed_file:
                        print(mf, file=f)
            for mf in self.malformed_file:
                print(mf)
            if trial_issues:
                raise LogFormatError("; ".join(self.malformed_file))

    def _ret_data(self, line, data):
        """Extract one or more 'Key: value,,' fields from a log line, converting
        each value to its natural type (datetime, tuple, bool, int, float, else
        string). Missing keys come back as 'Error'."""
        out_data = []
        if not isinstance(data, list):
            data = [data]
        for d in data:
            try:
                t_data = line.split(d + ": ")[1].split(",,")[0]
            except IndexError:
                try:
                    t_data = line.split(d + ":")[1].split(",,")[0]
                except IndexError:
                    try:
                        t_data = line.split(d + "=")[1].split(",,")[0]
                    except IndexError:
                        out_data.append("Error")
                        continue

            converted = None
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                        "%a %b %d %H:%M:%S %Y", "%H:%M:%S"):
                try:
                    converted = dt.datetime.strptime(t_data, fmt)
                    break
                except ValueError:
                    continue
            if converted is not None:
                out_data.append(converted)
                continue

            if t_data and t_data[0] == "(" and t_data[-1] == ")":
                out_data.append(tuple(int(x) for x in t_data[1:-1].split(", ")))
                continue
            if t_data == "True":
                out_data.append(True)
                continue
            if t_data == "False":
                out_data.append(False)
                continue
            try:
                out_data.append(int(t_data))
                continue
            except ValueError:
                pass
            try:
                out_data.append(float(t_data))
                continue
            except ValueError:
                pass
            try:
                out_data.append((int(t_data.split("x")[0]), int(t_data.split("x")[1])))
            except ValueError:
                out_data.append(t_data)  # keep as string
        return out_data


class FrameObj:
    def __init__(self, npy_file, pic_file, time, med, std, gp, lof):
        self.npy_file = npy_file
        self.pic_file = pic_file
        self.std_file = npy_file.replace("Frame_", "Frame_std_")
        self.time = time
        self.med = med
        self.std = std
        self.gp = gp
        self.lof = lof
        self.rel_day = 0
        self.frameDir = npy_file.replace(npy_file.split("/")[-1], "")
        self.index = int(npy_file.split("_")[1].split(".npy")[0]) - 1


class MovieObj:
    def __init__(self, time, movie_file, pic_file, framerate, resolution):
        self.startTime = time
        self.endTime = ""
        if ".mp4" in movie_file:
            self.mp4_file = movie_file
            self.h264_file = movie_file.replace(".mp4", "") + ".h264"
        else:
            self.h264_file = movie_file
            self.mp4_file = movie_file.replace(".h264", "") + ".mp4"
        self.pic_file = pic_file
        self.framerate = framerate
        self.movieDir = movie_file.replace(movie_file.split("/")[-1], "")
        self.baseName = self.mp4_file.split("/")[-1].replace(".mp4", "")
        self.height = resolution[1]
        self.width = resolution[0]
        self.index = int(movie_file.split("_vid")[0].split("/")[-1]) - 1


class Trial:
    def __init__(self, start_time, stop_time, reset_time, all_frames, all_movies, sampleID):
        self.startTime = start_time
        self.stopTime = stop_time
        self.resetTime = reset_time
        self.sampleID = sampleID

        self.frames = [x for x in all_frames if start_time < x.time < stop_time]
        self.daylight_frames = [x for x in self.frames if x.lof is True]

        self.reset_frame = None
        if reset_time is not None:
            after = [x for x in all_frames if x.time > reset_time]
            self.reset_frame = after[0] if after else None

        # Ensure every movie has an end time before comparing (open segments
        # default to 18:00 of their start day), then select this trial's movies.
        for movie in all_movies:
            if movie.endTime == "":
                movie.endTime = movie.startTime.replace(hour=18, minute=0)
        self.movies = [x for x in all_movies
                       if x.endTime > start_time and x.startTime < stop_time]

        # Group daylight frames into (first, last) pairs per calendar day.
        days = {}
        for frame in self.daylight_frames:
            frame.rel_day = (frame.time - all_frames[-1].time.replace(hour=0, minute=0)).days
            if frame.time.date() in days:
                days[frame.time.date()] = (days[frame.time.date()][0], frame)
            else:
                days[frame.time.date()] = (frame, frame)
        self.days = list(days.values())

        self.days_videos = []
        for start, stop in self.days:
            movie_idxs = [x.index for x in all_movies
                          if x.endTime > start.time and x.startTime < stop.time]
            self.days_videos.append(",".join(str(x) for x in movie_idxs))

        self.num_days = len(self.days)
        self.num_rows = int((self.num_days - 1) / 6) + 1 if self.num_days else 0

        for frame in self.frames:
            if not frame.lof:
                day_first = [x[1] for x in self.days if x[1].time < frame.time]
                day_second = [x[0] for x in self.days if x[0].time > frame.time]
                if day_first and day_second:
                    frame.nearest_day = (day_first[-1], day_second[0])