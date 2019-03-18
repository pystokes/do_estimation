#!/usr/bin/env python3
# -*- coding: UTF-8 -*-

import sys
import os
from pathlib import Path
import re
import jdcal
import numpy as np
import pandas as pd
import netCDF4
from tqdm import tqdm
from .lib import utils
from scipy import interpolate
from decimal import Decimal, ROUND_HALF_UP

from logging import DEBUG
from logging import getLogger

logger = getLogger('impulso')
logger.setLevel(DEBUG)

# Set HOME directory.
IMPULSO_HOME = os.environ['IMPULSO_HOME']


class Aggregator(object):

    def __init__(self, hparams):
        logger.info('Initialize Aggregator')
        self.hparams = hparams
        self.data_id = utils.issue_id()
        self.output_dir = Path(IMPULSO_HOME).joinpath(f'datasets/{self.data_id}')
        self.output_argo = self.output_dir.joinpath(f'argo.pkl')
        self.output_map = self.output_dir.joinpath(f'map.pkl')
        logger.info('Save hyperparameter')
        utils.save_hparams(self.output_dir, 'preprocess.yml', self.hparams)
        logger.info('End init of Aggregator')


    def generate_dataset(self):
        argo_info = []
        pre_profiles = []
        sal_profiles = []
        tem_profiles = []
        maps = []

        # Get SSH/SST data filename
        ssh_files = list(Path(self.hparams['input_data']['ssh_in_dir']).glob('*.nc'))
        sst_files = list(Path(self.hparams['input_data']['sst_in_dir']).glob('*.nc'))

        # Interpolate Argo profile by Akima method and crop related SSH/SST
        for file in tqdm(Path(self.hparams['input_data']['argo_in_dir']).glob('**/*.txt')):
            
            # Read all lines
            with open(file, 'r') as f:
                lines = f.readlines()

            # Reverse lines for pop() at the end of lines
            #   - pop() at the begging of list is too slow
            lines.reverse()

            # Begin reading profiles
            while lines:

                # Get profile information
                header = lines.pop()
                argo_date, argo_lat, argo_lon, n_layer = self.parse_argo_header(header)

                # Caluculate number of days elapsed from reference date
                n_days_elapsed = self.calc_days_elapsed(argo_date, self.hparams['preprocess']['reference_date'])

                # Get flags to check date and location of Argo and SSH/SST
                is_in_region = self.check_lat_and_lon(argo_lat, argo_lon)
                within_the_period = self.check_period(
                    argo_date,
                    self.hparams['argo_selection']['date']['min'],
                    self.hparams['argo_selection']['date']['max']
                )
                ssh_file = self.check_file_existance(argo_date, ssh_files)
                sst_file =  self.check_file_existance(argo_date, sst_files)

                # Skip a profile if related SSH/SST don't exists
                if not (is_in_region and within_the_period and ssh_file and sst_file):
                    for _ in range(n_layer + 2):
                        lines.pop()
                    continue
                else:
                    cropped_ssh = self.crop_map(argo_lat, argo_lon, ssh_file, 'ssh')
                    cropped_sst = self.crop_map(argo_lat, argo_lon, sst_file, 'sst')

                # Skip line with data label (line of 'pr sa te')
                lines.pop()

                # Get parameters of a profile
                pre_profile, sal_profile, tem_profile = [], [], []
                for _ in range(n_layer):
                    line = lines.pop()
                    pre, sal, tem = map(float, re.split(' +', line.replace('\n', '').lstrip(' ')))
                    pre_profile.append(pre)
                    sal_profile.append(sal)
                    tem_profile.append(tem)

                # Interpolate a profile by Akima method
                pre_min = self.hparams['preprocess']['interpolation']['min_pressure']
                pre_max = self.hparams['preprocess']['interpolation']['max_pressure']
                pre_interval = self.hparams['preprocess']['interpolation']['pressure_interval']
                pre_interpolated = list(range(pre_min, pre_max+pre_interval, pre_interval))
                sal_interpolated = self.interpolate_by_akima(pre_profile, sal_profile, pre_min, pre_max, pre_interval)
                tem_interpolated = self.interpolate_by_akima(pre_profile, tem_profile, pre_min, pre_max, pre_interval)

                # Store header data of Argo profile
                argo_info.append([n_days_elapsed, argo_lat, argo_lon])

                # Store profiles
                pre_profiles.append(pre_interpolated)
                sal_profiles.append(sal_interpolated)
                tem_profiles.append(tem_interpolated)

                # Store SSH/SST
                maps.append([cropped_ssh, cropped_sst])

                # Skip separater (line of '**')
                lines.pop()

        return np.array(argo_info), np.array(pre_profiles), np.array(sal_profiles), np.array(tem_profiles), np.array(maps)


    def parse_argo_header(self, header):
        argo_date = header[20:28]
        argo_lat = float(header[29:36])
        argo_lon = float(header[37:44])
        n_layer = int(header[44:48])

        return argo_date, argo_lat, argo_lon, n_layer


    def calc_days_elapsed(self, current_date, ref_date='2000-01-01'):
        """
        Args:
            current_date: YYYYMMDD (String)
            ref_date:     YYYY-MM-DD (String)
        """
        argo_jd = sum(jdcal.gcal2jd(current_date[:4], current_date[4:6], current_date[6:]))
        ref_jd = sum(jdcal.gcal2jd(ref_date.year, ref_date.month, ref_date.day))
        
        return int(argo_jd - ref_jd)


    def interpolate_by_akima(self, pre_profile, obj_profile, min_pressure, max_pressure, interval):
        func = interpolate.Akima1DInterpolator(pre_profile, obj_profile)
        return func(range(min_pressure, max_pressure+interval, interval))


    def check_lat_and_lon(self, argo_lat, argo_lon):
        lat_min = self.hparams['argo_selection']['latitude']['min']
        lat_max = self.hparams['argo_selection']['latitude']['max']
        lon_min = self.hparams['argo_selection']['longitude']['min']
        lon_max = self.hparams['argo_selection']['longitude']['max']

        if (lat_min <= argo_lat <= lat_max) and (lon_min <= argo_lon <= lon_max):
            return True
        else:
            return False


    def check_period(self, current_date, date_min, date_max):
        date_min = pd.to_datetime(date_min)
        date_max = pd.to_datetime(date_max)

        if date_min <= pd.to_datetime(current_date) <= date_max:
            return True
        else:
            return False


    def check_file_existance(self, argo_date, files):
        for file in files:
            if 'dm' + argo_date in file.name:
                return file
        return False


    def crop_map(self, argo_lat, argo_lon, map_file, data_type='ssh'):
        # Round Argo's latitude and longitude to 0.25 units
        argo_lat = Decimal(str(argo_lat * 4)).quantize(Decimal('0'), rounding=ROUND_HALF_UP) / 4
        argo_lon = Decimal(str(argo_lon * 4)).quantize(Decimal('0'), rounding=ROUND_HALF_UP) / 4

        zonal_dist = Decimal(int(self.hparams['preprocess']['crop']['zonal_distance_in_degree']))
        meridional_dist = Decimal(int(self.hparams['preprocess']['crop']['meridional_distance_in_degree']))

        # Get min/max index of latitude and longitude
        lat_min_idx, lat_max_idx = self.change_axis_to_index(argo_lat, meridional_dist, 'latitude')
        lon_min_idx, lon_max_idx = self.change_axis_to_index(argo_lon, zonal_dist, 'longitude')

        # Load data
        map = netCDF4.Dataset(map_file, 'r')

        # Crop
        if data_type == 'ssh':
            #scale_factor = map.variables['zos'].scale_factor
            #add_offset = map.variables['zos'].add_offset
            cropped = map.variables['zos'][0, lat_min_idx:lat_max_idx+1, lon_min_idx:lon_max_idx+1]
        elif data_type == 'sst':
            #scale_factor = map.variables['thetao'].scale_factor
            #add_offset = map.variables['thetao'].add_offset
            cropped = map.variables['thetao'][0, 0, lat_min_idx:lat_max_idx+1, lon_min_idx:lon_max_idx+1]
        else:
            logger.info('Map data type is not appropriate. Use default type (SSH)')
            #scale_factor = map.variables['zos'].scale_factor
            #add_offset = map.variables['zos'].add_offset
            cropped = map.variables['zos'][0, lat_min_idx:lat_max_idx+1, lon_min_idx:lon_max_idx+1]

        # Scale factor and offset
        """
        Add a process if need to use scale_factor and add_offset
        """

        # Fill missilng values
        cropped[cropped.mask] = 0.0

        return cropped


    def change_axis_to_index(self, argo_axis, width, data_type):
        # Latitude  : index=0 -> -83.0 degree, index=691 -> 89.75 degree
        # Longitude : index=0 -> XX degree, index=1439 -> XX degree
        if data_type == 'latitude':
            min_idx = int(((argo_axis - width / 2) + 83) * 4)
            max_idx = int(((argo_axis + width / 2) + 83) * 4)
        elif data_type == 'longitude':
            min_idx = int((argo_axis - width / 2) * 4)
            max_idx = int((argo_axis + width / 2) * 4)
        else:
            sys.exit('Error in "change_axis_to_index" function. Inappropriate "data_type"')
        
        return min_idx, max_idx



if __name__ == '__main__':
    pass
