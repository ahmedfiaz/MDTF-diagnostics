'''
This file is part of the precip_buoy_diag module of the MDTF code
package (see mdtf/MDTF-diagnostics/LICENSE.txt).

DESCRIPTION: Provides functions used by precip_buoy_diag_main.py

REQUIRED MODULES:

AUTHORS: Fiaz Ahmed

'''

import os
import glob
from sys import exit
import datetime as dt
import numpy as np
import numba
from numba import jit
import scipy.io
from scipy.interpolate import NearestNDInterpolator
from vert_cython import find_closest_index_2D, compute_layer_thetae
import xarray as xr
import matplotlib
import matplotlib.pyplot as plt
# from mpl_toolkits.mplot3d import Axes3D


# ======================================================================
# precipbuoy_binThetae
#  takes arguments and bins by subsat+ cape & bint

@jit(nopython=True) 
def precipbuoy_binThetae(lon_idx, REGION, PRECIP_THRESHOLD, NUMBER_CAPE_BIN, NUMBER_SUBSAT_BIN, 
NUMBER_BL_BIN, CAPE, SUBSAT, BL, RAIN, p0, p1, p2, pe, q0, q1, q2, qe):
 
 
    for lat_idx in np.arange(SUBSAT.shape[1]):
        subsat_idx=SUBSAT[:,lat_idx,lon_idx]
        cape_idx=CAPE[:,lat_idx,lon_idx]
        bl_idx=BL[:,lat_idx,lon_idx]
        rain=RAIN[:,lat_idx,lon_idx]
        reg=REGION[lon_idx,lat_idx]
        
        if reg>0:
            for time_idx in np.arange(SUBSAT.shape[0]):
                if (cape_idx[time_idx]<NUMBER_CAPE_BIN and cape_idx[time_idx]>=0 
                and subsat_idx[time_idx]<NUMBER_SUBSAT_BIN and subsat_idx[time_idx]>=0
                and np.isfinite(rain[time_idx])):
                    p0[subsat_idx[time_idx],cape_idx[time_idx]]+=1
                    p1[subsat_idx[time_idx],cape_idx[time_idx]]+=rain[time_idx]
                    p2[subsat_idx[time_idx],cape_idx[time_idx]]+=rain[time_idx]**2
                    
                    if (rain[time_idx]>PRECIP_THRESHOLD):
                        pe[subsat_idx[time_idx],cape_idx[time_idx]]+=1

                    
                if (bl_idx[time_idx]<NUMBER_BL_BIN and bl_idx[time_idx]>=0
                and np.isfinite(rain[time_idx])):
                    q0[bl_idx[time_idx]]+=1
                    q1[bl_idx[time_idx]]+=rain[time_idx]
                    q2[bl_idx[time_idx]]+=rain[time_idx]**2
                    if (rain[time_idx]>PRECIP_THRESHOLD):
                        qe[bl_idx[time_idx]]+=1
                        

class precipbuoy:

    def __init__(self):
        ### read in the primary input variable paths
        
        ### flag to check if a pre-processed file exists
        if glob.glob(os.environ["temp_file"]):
            self.preprocessed=True
        else:
            self.preprocessed=False 
            
        ### flag to check if binned output exists ###
        if glob.glob(os.environ["binned_output"]):
            self.binned=True
        else:
            self.binned=False 
        
        ### set time and latitudinal slices here ###
        ### the analysis will only occur over this subset ###
        strt_dt=dt.datetime.strptime(str(os.environ['FIRSTYR'])+'010100',"%Y%m%d%H")
        end_dt=dt.datetime.strptime(str(os.environ['LASTYR'])+'123123',"%Y%m%d%H")
        
        self.time_slice=slice(strt_dt, end_dt)  ## set time slice       
        self.lat_slice=slice(-20,20) ## set latitudinal slice
        
        ### Ensure that start and end dates span more than 1 day.
        if (self.time_slice.stop-self.time_slice.start).days<1:
            exit('Please set time range greater than 1 day. Exiting now')

        
        ### Format for datetime conversion 
        self.DATE_FORMAT='%Y%m%d'
        
        ### rename dimensions to internal names for ease of use
        self.NEW_VARS={'time':'time','lev':'lev','lat':'lat','lon':'lon'}


         
            
    # #  Function preprocess takes in 3D tropospheric temperature and specific humidity fields on model levels, 
    # # and calculates: thetae_LFT, thetae_sat_LFT & thetae_BL.
 
    def preprocess(self):
        ### LOAD temp. and q datasets ###
        ta_ds=xr.open_mfdataset(os.environ['ta_file'])
        hus_ds=xr.open_mfdataset(os.environ['hus_file'])
 
 
        print("....SLICING DATA")

        ta_ds_subset=self._slice_data(ta_ds)
        hus_ds_subset=self._slice_data(hus_ds)


        ### check to ensure that time subsets are non-zero ###
        assert ta_ds_subset.time.size>0 , 'specified time range is zero!!'
            
        ### Load arrays into memory ###
    
        lat=ta_ds_subset['lat']
        lon=ta_ds_subset['lon']
        ta=ta_ds_subset[os.environ['ta_var']]
        hus=hus_ds_subset[os.environ['qa_var']]
        lev=ta_ds_subset['lev']
        
        
        ### Is surface pressure is available, extract it
        ### if not set ps_ds to None
        if os.environ['ps_file']:
            ps_ds=xr.open_mfdataset(os.environ['ps_file'])
            ps_ds_subset=self._slice_data(ps_ds,dimsize=3)

        else:
            ps_ds_subset=None

        ### extract pressure levels 
        pres,ps=self._return_pres_levels(lev,ta,ps_ds_subset)  
    
        assert(ta['time'].size==hus['time'].size)

        ### setting parameters for buoyancy calculations
    
        ## setting the pressure level at the top of a nominal boundary layer
        pbl_top=ps-100e2 ## The BL is 100 mb thick ##
        pbl_top=np.float_(pbl_top.values.flatten()) ### overwriting pbl top xarray with numpy array
        
        ## setting the pressure level at the top of a nominal lower free troposphere (LFT) 
        low_top=np.zeros_like(ps)
        low_top[:]=500e2  # the LFT top is fixed at 500 mb
        low_top=np.float_(low_top.flatten())

        ### LOAD data arrays into memory###
        print('...LOADING ARRAYS INTO MEMORY')
        ta=ta.transpose('lev','time','lat','lon')
        hus=hus.transpose('lev','time','lat','lon')
        pres=pres.transpose('lev','time','lat','lon')
        ps=ps.transpose('time','lat','lon')

        pres=pres.values   
        ta=np.asarray(ta.values,dtype='float')
        hus=np.asarray(hus.values,dtype='float')

        print('...DONE LOADING')

        ta_ds.close()
        hus_ds.close()
        
        ### Check if pressure array is descending 
        ### since this is an implicit assumption

        if (np.all(np.diff(pres,axis=0)<0)):
            print('     pressure levels strictly decreasing')
        elif (np.all(np.diff(pres,axis=0)>0)):
            print('     pressure levels strictly increasing')
            print('     reversing the pressure dimension')
            pres=pres[::-1,:,:,:]
            ta=ta[::-1,:,:,:]
            hus=hus[::-1,:,:,:]
        else:
            exit('......Check pressure level ordering. Exiting now..')

        ### Reshape arrays to 2D ###
        
        print('...COMPUTING THETAE VARIABLES')

        lev=pres.reshape(*lev.shape[:1],-1)
        ta_flat=ta.reshape(*ta.shape[:1],-1)
        hus_flat=hus.reshape(*hus.shape[:1],-1)

        pbl_ind=np.zeros(pbl_top.size,dtype=np.int64)
        low_ind=np.zeros(low_top.size,dtype=np.int64)

        ### Find the closest pressure level to pbl_top and low_top
        ### using a cython routine 'find_closest_index_2D'
        find_closest_index_2D(pbl_top,lev,pbl_ind)
        find_closest_index_2D(low_top,lev,low_ind)

        ### Declare empty arrays to hold thetae variables
        thetae_bl=np.zeros_like(pbl_top)
        thetae_lt=np.zeros_like(pbl_top)
        thetae_sat_lt=np.zeros_like(pbl_top)

        ### the fractional weighting of the boundary layer in 
        ### buoyancy computation
        wb=np.zeros_like(pbl_top)

        ### Use trapezoidal rule for approximating the vertical integral ###
        ### vert. integ.=(b-a)*(f(a)+f(b))/2
        ### using a cython routine 'compute_layer_thetae'
        compute_layer_thetae(ta_flat, hus_flat, lev, pbl_ind, low_ind, thetae_bl, thetae_lt, thetae_sat_lt, wb)

        ### if thetae_bl is zero set it to nan
        ### masking is an option.
        thetae_bl[thetae_bl==0]=np.nan
        thetae_lt[thetae_lt==0]=np.nan
        thetae_sat_lt[thetae_sat_lt==0]=np.nan
        
        ### Unflatten the space dimension to lat,lon ###
        thetae_bl=thetae_bl.reshape(ps.shape)
        thetae_lt=thetae_lt.reshape(ps.shape)
        thetae_sat_lt=thetae_sat_lt.reshape(ps.shape)

        print('.....'+os.environ['ta_file']+" & "+os.environ['hus_file']+" pre-processed!")

        ### SAVING INTERMEDIATE FILE TO DISK ###

        data_set=xr.Dataset(data_vars={"thetae_bl":(ta_ds_subset[os.environ['ta_var']].isel(lev=0).dims, thetae_bl),
                              "thetae_lt":(ta_ds_subset[os.environ['ta_var']].isel(lev=0).dims, thetae_lt),
                              "thetae_sat_lt":(ta_ds_subset[os.environ['ta_var']].isel(lev=0).dims, thetae_sat_lt),
                              "ps":(ta_ds_subset[os.environ['ta_var']].isel(lev=0).dims, ps)},
                   coords=ta_ds_subset[os.environ['ta_var']].isel(lev=0).drop('lev').coords)
        data_set.thetae_bl.attrs['long_name']="theta_e averaged in the BL (100 hPa above surface)"
        data_set.thetae_lt.attrs['long_name']="theta_e averaged in the LFT (100 hPa above surface to 500 hPa)"
        data_set.thetae_sat_lt.attrs['long_name']="theta_e_sat averaged in the LFT (100 hPa above surface to 500 hPa)"
        data_set.ps.attrs['long_name']="surface pressure"

        data_set.thetae_bl.attrs['units']="K"
        data_set.thetae_lt.attrs['units']="K"
        data_set.thetae_sat_lt.attrs['units']="K"
        data_set.ps.attrs['units']='Pa'

        data_set.attrs['source']="Precipiation Buoyancy Diagnostics \
        - as part of the NOAA Model Diagnostic Task Force (MDTF)"

        data_set.to_netcdf(os.environ["temp_file"],mode='w')
        print('...'+os.environ["temp_file"]+" saved!")
        
        ### set preprocessed flag to True
        if glob.glob(os.environ["temp_file"]):
            self.preprocessed=True 

        


    ### function to fix datetime formats 
    def _fix_datetime(self, ds,date_format=None):
        try:      
            if ds.indexes['time'].dtype=='float64' or ds.indexes['time'].dtype=='int64':
                ds['time']=[dt.datetime.strptime(str(int(i.values)),date_format) for i in ds.time]
            else:
                datetimeindex = ds.indexes['time'].to_datetimeindex()
                ds['time'] = datetimeindex
        except:
            pass
    
    def _slice_data(self,ds,dimsize=4):
        '''
        This function can open file, fix co-ordinate names, 
        and slice data for each variable
        '''

        LAT_VAR_NEW=self.NEW_VARS['lat']
        LON_VAR_NEW=self.NEW_VARS['lon']
        TIME_VAR_NEW=self.NEW_VARS['time']
        LEV_VAR_NEW=self.NEW_VARS['lev']
        
        if dimsize==4:
            ds=ds.rename({os.environ['time_coord']:TIME_VAR_NEW,os.environ['lat_coord']:LAT_VAR_NEW,os.environ['lon_coord']:LON_VAR_NEW,
            os.environ['lev_coord']:LEV_VAR_NEW})

        elif dimsize==3:
            ds=ds.rename({os.environ['time_coord']:TIME_VAR_NEW,os.environ['lat_coord']:LAT_VAR_NEW,os.environ['lon_coord']:LON_VAR_NEW})
            
                
        ### Ensure that times are in datetime format ###
        self._fix_datetime(ds, self.DATE_FORMAT)

        ### select subset ###
        ds_subset=ds.sel(time=self.time_slice,lat=self.lat_slice)

        return ds_subset


    def _return_pres_levels(self, lev, da, ps_ds):
        
        '''
        Function to set pressure levels and surface pressure
        depending on whether incoming levels are on pressure or sigma co-ordinates.     
        '''
        
    ### Check if pressure or sigma co-ordinates ###

        if os.environ['VERT_TYPE']=='pres':

            pres=lev
            ### Check if units are in hPa (or mb)
            ### Convert units to Pa if required ###
            if(str(pres.units).lower() in [i.lower() for i in ['hPa','mb']]):
                pres=pres*100
                
            ### Convert data type
            pres=pres.astype('float')
            
            
            ## broadcast pressure to a 4D array to mimic sigma levels
            ## this step is computationally inefficient, but helps retain 
            ## portability between pressure and sigma level handling
            
            pres,dummy=xr.broadcast(pres,da.isel(lev=0,drop=True))             


            ### Read surface pressure values if available          
            if ps_ds:
                ps=ps_ds[os.environ['ps_var']]
                if(ps.units=='hPa'):
                    ps=ps*100
            ### if unavailable, set surface pressure to maximum pressure level
            else:        
                ps=pres.sel(lev=lev.max().values)

        
        elif os.environ['VERT_TYPE']=='sigma':
            ### currently written so that coefficients a and b are 
            ### stored in the surface pressure file
            a=ps_ds[os.environ['a_var']]
            b=ps_ds[os.environ['b_var']] 
            ps=ps_ds[os.environ['ps_var']]
            
            ### Create pressure data ###
            pres=b*ps+a     

        return pres, ps
 
 
     # ======================================================================
    # generate_region_mask: function provided by Yi-Hung Kuo
    #  generates a map of integer values that correspond to regions using
    #  the file region_0.25x0.25_costal2.5degExcluded.mat 
    #  in var_data/convective_transition_diag
    # Currently, there are 4 regions corresponding to ocean-only grid points
    #  in the Western Pacific (WPac), Eastern Pacific (EPac),
    #  Atlantic (Atl), and Indian (Ind) Ocean basins
    # Coastal regions (within 2.5 degree with respect to sup-norm) are excluded
    # 

    def _generate_region_mask(self, region_mask_filename, ds):
    
        print("...generating region mask..."),

        # Load & Pre-process Region Mask
        matfile=scipy.io.loadmat(region_mask_filename)
        lat_m=matfile["lat"]
        lon_m=matfile["lon"] # 0.125~359.875 deg
        region=matfile["region"]
        lon_m=np.append(lon_m,np.reshape(lon_m[0,:],(-1,1))+360,0)
        lon_m=np.append(np.reshape(lon_m[-2,:],(-1,1))-360,lon_m,0)
        region=np.append(region,np.reshape(region[0,:],(-1,lat_m.size)),0)
        region=np.append(np.reshape(region[-2,:],(-1,lat_m.size)),region,0)

        LAT,LON=np.meshgrid(lat_m,lon_m,sparse=False,indexing="xy")
        LAT=np.reshape(LAT,(-1,1))
        LON=np.reshape(LON,(-1,1))
        REGION=np.reshape(region,(-1,1))

        LATLON=np.squeeze(np.array((LAT,LON)))
        LATLON=LATLON.transpose()

        regMaskInterpolator=NearestNDInterpolator(LATLON,REGION)

        # Interpolate Region Mask onto Model Grid using Nearest Grid Value
#         pr_netcdf=Dataset(model_netcdf_filename,"r")
        lon=ds.lon.values
        lat=ds.sel(lat=self.lat_slice).lat.values
        if lon[lon<0.0].size>0:
            lon[lon[lon<0.0]]+=360.0

        LAT,LON=np.meshgrid(lat,lon,sparse=False,indexing="xy")
        LAT=np.reshape(LAT,(-1,1))
        LON=np.reshape(LON,(-1,1))
        LATLON=np.squeeze(np.array((LAT,LON)))
        LATLON=LATLON.transpose()
        REGION=np.zeros(LAT.size)
        for latlon_idx in np.arange(REGION.shape[0]):
            REGION[latlon_idx]=regMaskInterpolator(LATLON[latlon_idx,:])
        REGION=np.reshape(REGION.astype(int),(-1,lat.size))
    
        print("...Generated!")

        return REGION

 
    def bin(self):
    
        ### Define binning parameters ###
        ### Currently set inside the POD; a flexible option would 
        ### be to read user-defined values. This would be useful,
        ### if a model state space is different from usually encountered.
    
        bl_bin_params={}
        bl_bin_params['width']=0.01
        bl_bin_params['max']=1.50
        bl_bin_params['min']=-1.5
    
        # Bin width and intervals for CAPE and SUBSAT.
        # In units of K
        cape_params_list=[1,20,-40]
        subsat_params_list=[1,42,-1]

        cape_bin_params={key: value for key,value in zip(bl_bin_params.keys(),cape_params_list)}
        subsat_bin_params={key: value for key,value in zip(bl_bin_params.keys(),subsat_params_list)}
    
        generate_bin_center = lambda x: np.arange(x['min'],x['max']+x['width'],x['width'])

        cape_bin_center=generate_bin_center(cape_bin_params)
        subsat_bin_center=generate_bin_center(subsat_bin_params)
        bl_bin_center=generate_bin_center(bl_bin_params)
    
        NUMBER_CAPE_BIN=cape_bin_center.size
        NUMBER_SUBSAT_BIN=subsat_bin_center.size
        NUMBER_BL_BIN=bl_bin_center.size
    
        # Allocate arrays for 2D binning (CAPE, SUBSAT)
        P0=np.zeros((NUMBER_SUBSAT_BIN,NUMBER_CAPE_BIN))
        P1=np.zeros((NUMBER_SUBSAT_BIN,NUMBER_CAPE_BIN))
        P2=np.zeros((NUMBER_SUBSAT_BIN,NUMBER_CAPE_BIN))
        PE=np.zeros((NUMBER_SUBSAT_BIN,NUMBER_CAPE_BIN))

        # Allocate arrays for 1D binning (BL)

        Q0=np.zeros((NUMBER_BL_BIN))
        Q1=np.zeros((NUMBER_BL_BIN))
        Q2=np.zeros((NUMBER_BL_BIN))
        QE=np.zeros((NUMBER_BL_BIN))
    
        ### Internal constants ###
    
        ref_thetae=340 ## reference theta_e in K to convert buoy. to temp units
        gravity=9.8 ### accl. due to gravity
        thresh_pres=700 ## Filter all point below this surface pressure in hPa
    
        ## Open and slice precip. data ###     
        print('LOADING thetae and pcp. values')           
        pr_ds=xr.open_mfdataset(os.environ["pr_file"])    
        pr_ds_subset=self._slice_data(pr_ds,dimsize=3)

        thetae_ds=xr.open_mfdataset(os.environ["temp_file"])
        
        thetae_bl=thetae_ds.thetae_bl.values
        thetae_lt=thetae_ds.thetae_lt.values
        thetae_sat_lt=thetae_ds.thetae_sat_lt.values
  
        ps=thetae_ds.ps.values        
        ps=ps*1e-2 ## Convert surface pressure to hPa

        pr=pr_ds_subset[os.environ['pr_var']].values*np.float(os.environ['pr_conversion_factor'])        
        ###
        pr[pr<0]=0.0 ##in case model has spurious negative precipitation.
        
        ### generate a mask for land points ###
        ### using region_mask function from convective transition statistics POD ###
        
        REGION=self._generate_region_mask(os.environ["region_mask"],pr_ds_subset)
        
        ### get parameters of buoyancy computation ###
        ### see Ahmed et al. 2020, JAS for computation ##        
        delta_pl=ps-100-500
        delta_pb=100
        wb=(delta_pb/delta_pl)*np.log((delta_pl+delta_pb)/delta_pb)
        wl=1-wb

        ### points with surface pressure below threshold are set to nan
        wb[ps<thresh_pres]=np.nan
        wl[ps<thresh_pres]=np.nan

        ### compute cape, subsat and bl
        cape=ref_thetae*(thetae_bl-thetae_sat_lt)/thetae_sat_lt
        subsat=ref_thetae*(thetae_sat_lt-thetae_lt)/thetae_sat_lt
        bl=gravity*(wb*(thetae_bl-thetae_sat_lt)/thetae_sat_lt-wl*(thetae_sat_lt-thetae_lt)/thetae_sat_lt)

        cape[ps<thresh_pres]=np.nan
        subsat[ps<thresh_pres]=np.nan

    
        ### Get indices ###
        SUBSAT=(subsat-subsat_bin_params['min'])/subsat_bin_params['width']-0.5
        SUBSAT=SUBSAT.astype(int)
    
        CAPE=(cape-cape_bin_params['min'])/cape_bin_params['width']-0.5
        CAPE=CAPE.astype(int)

        BL=(bl-bl_bin_params['min'])/(bl_bin_params['width'])+0.5
        BL=BL.astype(int)

        RAIN=pr        
        RAIN[RAIN<0]=0 # Sometimes models produce negative rain rates

        # Binning is structured in the following way to avoid potential round-off issue
        #  (an issue arise when the total number of events reaches about 1e+8)
        
        p0=np.zeros((NUMBER_SUBSAT_BIN,NUMBER_CAPE_BIN))
        p1=np.zeros_like(p0)
        p2=np.zeros_like(p0)
        pe=np.zeros_like(p0)
                            
        q0=np.zeros((NUMBER_BL_BIN))
        q1=np.zeros((NUMBER_BL_BIN))
        q2=np.zeros((NUMBER_BL_BIN))
        qe=np.zeros((NUMBER_BL_BIN))

        
        ## numba does not run in nopython mode when reading environment variables
        ## therefore transferring to float here.
        PRECIP_THRESHOLD=np.float(os.environ['PRECIP_THRESHOLD'])
        
        print("...Binning...")
        for lon_idx in np.arange(SUBSAT.shape[2]):
                
            
            precipbuoy_binThetae(lon_idx, REGION, PRECIP_THRESHOLD,
            NUMBER_CAPE_BIN, NUMBER_SUBSAT_BIN, NUMBER_BL_BIN, 
            CAPE, SUBSAT, BL, RAIN, p0, p1, p2, pe, q0, q1, q2, qe)

            P0+=p0
            P1+=p1
            P2+=p2
            PE+=pe
        
            Q0+=q0
            Q1+=q1
            Q2+=q2
            QE+=qe
        
            ### Re-set the array values to zero ###
            p0[:]=0
            q0[:]=0

            p1[:]=0
            q1[:]=0

            p2[:]=0
            q2[:]=0

            pe[:]=0
            qe[:]=0
            
        print("...Binning complete. Saving to file...")

        data_set=xr.Dataset(data_vars={'P0': (('subsat','cape'), P0),
                                       'PE': (('subsat','cape'), PE),
                                       'P1': (('subsat','cape'), P1),
                                       'P2': (('subsat','cape'), P2),
                                       'Q0': (('bint'),Q0),
                                       'QE': (('bint'),QE),
                                       'Q1': (('bint'),Q1),
                                       'Q2': (('bint'),Q2)},
                            coords={'subsat':subsat_bin_center,
                                    'cape':cape_bin_center,
                                    'bl':bl_bin_center})
    
        data_set.subsat.attrs['units']="K"
        data_set.cape.attrs['units']="K"
        data_set.bint.attrs['units']="m/s^2"
    
        data_set.P1.attrs['units']="mm/hr"
        data_set.P2.attrs['units']="mm^2/hr^2"
    
        data_set.Q1.attrs['units']="mm/hr"
        data_set.Q2.attrs['units']="mm^2/hr^2"
        
        data_set.attrs['source']="Precipitation buoyancy diagnostics package \
        - as part of the NOAA Model Diagnostic Task Force (MDTF) effort"
    
        ### Manually clobbering since .to_netcdf throws permission errors ###
    
        try:
            os.remove(os.environ["binned_output"])
        except:
            pass
    
        data_set.to_netcdf(os.environ["binned_output"],
        mode='w',
        engine='netcdf4')
    
        print("   Binned results saved as "+os.environ["binned_output"]+'!')
        if glob.glob(os.environ["binned_output"]):
            self.binned=True
        
            
    def plot(self):
    
        ### internal variable: minimum of samples with which to 
        ### construct conditional means
        NUMBER_THRESHOLD=50
        
        ### Create obs. binned precip. ###  
        ds_obs=xr.open_dataset(os.environ["binned_obs"])
  
        P0_obs=ds_obs.P0.values
        P1_obs=ds_obs.P1.values
        PE_obs=ds_obs.PE.values
        cape_bin_center_obs, subsat_bin_center_obs=ds_obs.cape.values, ds_obs.subsat.values 

        P0_obs[P0_obs==0.0]=np.nan
        P_obs=P1_obs/P0_obs
        P_obs[P0_obs<NUMBER_THRESHOLD]=np.nan

        bl_center_obs=ds_obs['bint'].values
        Q0,Q1=ds_obs['Q0'].values,ds_obs['Q1'].values
        Q0[Q0==0.0]=np.nan
        Q_obs=Q1/Q0
        Q_obs[Q0<NUMBER_THRESHOLD]=np.nan

        ### Create model binned precip. ###    

        ds_model=xr.open_dataset(os.environ['binned_output'])
        
        P0=ds_model.P0.values
        P1=ds_model.P1.values
        PE_model=ds_model.PE.values

        P0[P0==0.0]=np.nan
        P_model=P1/P0
        P_model[P0<NUMBER_THRESHOLD]=np.nan
        cape_bin_center_model, subsat_bin_center_model=ds_model.cape.values, ds_model.subsat.values 

        bl_center_model=ds_model['bl'].values
        Q0,Q1=ds_model['Q0'].values,ds_model['Q1'].values
        Q0[Q0==0.0]=np.nan
        Q_model=Q1/Q0
        Q_model[Q0<NUMBER_THRESHOLD]=np.nan


        ### Compute Tq ratio ###
        ### Measure precip. sensitivity to CAPE vs. SUBSAT     
        gamma_Tq={}
        gamma_Tq['OBS']=self._calcqT_ratio(P_obs,PE_obs,cape_bin_center_obs,subsat_bin_center_obs)
        gamma_Tq[os.environ['CASENAME']]=self._calcqT_ratio(P_model,PE_model,cape_bin_center_model,subsat_bin_center_model)


        print("...Plotting Surfaces..."),


        ### Create 2D surface plotting parameters
        ### Flexible option to read parameters from user could be implemented
        
        fig_params={}
        
        axes_fontsize = 13 # size of font in all plots
        axes_elev= 20 # 30 elevation for 3D plot
        axes_azim=300 # 300 azimuthal angle for 3D plot
        labelsize=13
        

        figsize1 = 9.0 # figure size set by figsize=(figsize1,figsize2)
        figsize2 = 7 
        
        fig_params['f0']=[axes_fontsize,axes_elev,axes_azim,labelsize]

        xlim=[0,40] 
        ylim=[-40,0]
        zlim=[0,7]
        
        ### Plotting labels ##
        xlabel="$\mathrm{SUBSAT}_\mathrm{L}$ (K)"
        ylabel="$\mathrm{CAPE}_\mathrm{L}$ (K)"
        zlabel="Precip. (mm/hr)"
        fig_title='ERA5/TRMM3B42'
        
        fig_params['f1']=[xlim, ylim, zlim, xlabel, ylabel, zlabel, fig_title]

        fig = plt.figure(figsize=(figsize1,figsize2))

        ### Plot ERA5/TRMM 3B42 (Obs.) ###
        ax = fig.add_subplot(121, projection='3d')
        self._plot_precip_surface(fig, ax, subsat_bin_center_obs, cape_bin_center_obs, P_obs, 
                                  fig_params)

        ax.text2D(0.7,.6,'$\gamma_{Tq}$=%.2f'%(gamma_Tq['OBS']),transform=ax.transAxes,
        fontsize=14)

        ### Plot Model ###
        fig_params['f1'][6]=os.environ['CASENAME']
        ax = fig.add_subplot(122, projection='3d')
        
        self._plot_precip_surface(fig, ax, subsat_bin_center_model, cape_bin_center_model, P_model, 
                            fig_params, subsat_bin_center_obs, cape_bin_center_obs, P_obs, 
                            plot_ref=True, plot_cbar=True, cbar_coords=[1.07,0.35,.75,0.03])

        ax.text2D(0.7,.6,'$\gamma_{Tq}$=%.2f'%(gamma_Tq[os.environ['CASENAME']]),
        transform=ax.transAxes,fontsize=14)

        plt.tight_layout()
        plt.savefig("{WK_DIR}/model/{CASENAME}.PrecipBuoySurf.png".format(**os.environ), bbox_inches="tight")
        
        print("...Completed!")
        print("...Precipitation surface plots saved as {WK_DIR}/model/{CASENAME}.PrecipBuoySurf.png!".format(**os.environ))

        
        figsize1 = 4.0 # figure size set by figsize=(figsize1,figsize2)
        figsize2 = 4 
        fig,ax=plt.subplots(1,1,figsize=(figsize1,figsize2))
        
        ax.scatter(bl_center_model,Q_model,marker='D',color='blue',s=20,label='{CASENAME}'.format(**os.environ))
        ax.scatter(bl_center_obs,Q_obs,marker='*',color='grey',s=20,label='ERA5/TRMM3B42')

        ax.tick_params(which='both',labelsize=labelsize-2)
        ax.set_ylabel(zlabel,fontsize=axes_fontsize-1)
        ax.set_xlabel('$B_L$ ($\mathrm{ms^{-2}}$)',fontsize=axes_fontsize-1)
        ax.set_ylim(zlim)
        ax.set_title('Precipitation vs. buoyancy',fontsize=axes_fontsize+1)
        
        leg=ax.legend(fontsize=11.,ncol=1,loc="upper left")
        frame=leg.get_frame()
        frame.set_edgecolor('black')
        
        plt.tight_layout()
        plt.savefig("{WK_DIR}/model/{CASENAME}.PrecipBuoyCurve.png".format(**os.environ), bbox_inches="tight")
        print("...Precipitation buoyancy curve plots saved as {WK_DIR}/model/{CASENAME}.PrecipBuoyCurve.png!".format(**os.environ))

    def _calcqT_ratio(self, Z, counts,cape_bin_center,subsat_bin_center):
        '''
        Function that takes the precipitation surface and produces an estimate of the 
        temperature-to-moisture sensitivity. This metric measures the rate of precipitation
        increase along the CAPE direction and compares to the corresponding increase along the
        SUBSAT direction.
        '''

        ### Find the location of max counts. This is generally near the precipitation
        ### onset.
        subsat_max_pop_ind,cape_max_pop_ind=np.where(counts==np.nanmax(counts))

        ### Create three copies of the 2D precipitation surface array.
        ### Divide the precipitation surface into three portions: the CAPE, SUBSAT and
        ### overlapping portions 
        ### The CAPE portion is for SUBSAT values beyond the SUBSAT index of max counts
        ### The SUBSAT portion is for CAPE values beyond the CAPE index of max counts
        ### The overlapping portion contains the overlapping components of the CAPE and SUBSAT arrays.
    
        Z_subsat=np.copy(Z)
        Z_subsat[:]=np.nan
        Z_subsat[subsat_max_pop_ind[0]-1:,cape_max_pop_ind[0]:]=Z[subsat_max_pop_ind[0]-1:,cape_max_pop_ind[0]:]

        Z_cape=np.copy(Z)
        Z_cape[:]=np.nan
        Z_cape[:subsat_max_pop_ind[0],:cape_max_pop_ind[0]+1]=Z[:subsat_max_pop_ind[0],:cape_max_pop_ind[0]+1]

        Z_overlap=np.copy(Z)
        Z_overlap[:]=np.nan
        Z_overlap[:subsat_max_pop_ind[0],cape_max_pop_ind[0]:]=Z[:subsat_max_pop_ind[0],cape_max_pop_ind[0]:]


        ### Get the average cape and subsat values for each of the three regions
        fin0=(np.where(np.isfinite(Z_overlap)))
        fin1=(np.where(np.isfinite(Z_cape)))
        fin2=(np.where(np.isfinite(Z_subsat)))

        subsat_y0=subsat_bin_center[fin0[0]]
        cape_x0=cape_bin_center[fin0[1]]
    
#         subsat_y1=subsat_bin_center[fin1[0]]
        cape_x1=cape_bin_center[fin1[1]]

        subsat_y2=subsat_bin_center[fin2[0]]
#         cape_x2=cape_bin_center[fin2[1]]

    
        ### Get a distance measure between the overlapping region to the cape and subsat regions

        dcape=abs(cape_x0.mean()-cape_x1.mean())
        dsubsat=abs(subsat_y0.mean()-subsat_y2.mean())
        
        ### Get a distance measure between the overlapping region to the cape and subsat regions
        ### Compute the average precipitation within the CAPE and SUBSAT regions. 

        area_cape=np.nanmean(Z_cape)
        area_subsat=np.nanmean(Z_subsat)
        area_overlap=np.nanmean(Z_overlap)
        darea_cape=abs(area_overlap-area_cape)
        darea_subsat=abs(area_overlap-area_subsat)
        ratio=darea_cape*dsubsat/(dcape*darea_subsat)
    
        return ratio

    def _plot_precip_surface(self, fig, ax, xbin, ybin, Z, fig_params,
                            xbin_ref=None, ybin_ref=None, Z_ref=None, plot_ref=False,
                            plot_cbar=False,cbar_coords=[1.03,0.35,1.0,0.05]):

        normed=matplotlib.colors.Normalize(vmin=fig_params['f1'][2][0],
                                           vmax=fig_params['f1'][2][1])
    
        X, Y = np.meshgrid(xbin,ybin)
    
        interval = np.linspace(0, 0.95)
        colors_trunc = matplotlib.cm.nipy_spectral(interval)
        cmap = matplotlib.colors.LinearSegmentedColormap.from_list('name', colors_trunc)
    
        colors=cmap(normed(Z.T))
        surf=ax.plot_surface(X,Y,Z.T,facecolors=colors,alpha=0.75)
    
        if plot_ref:
            X_ref, Y_ref = np.meshgrid(xbin_ref,ybin_ref)
            ax.plot_surface(X_ref,Y_ref,Z_ref.T,color='black',alpha=0.25)

        
        ### Fix to avoid plotting error ###
        for spine in ax.spines.values():
            spine.set_visible(False)

        ax.set_zlim(fig_params['f1'][2])

        ### Set the x and y limits to span the union of both obs and model bins
        ax.set_xlim(fig_params['f1'][0])
        ax.set_ylim(fig_params['f1'][1])



        ax.set_xlabel(fig_params['f1'][3],fontsize=fig_params['f0'][0])
        ax.set_ylabel(fig_params['f1'][4],fontsize=fig_params['f0'][0])
        ax.set_zlabel(fig_params['f1'][5],fontsize=fig_params['f0'][0])
        ax.view_init(elev=fig_params['f0'][1], azim=fig_params['f0'][2])
        ax.set_title(fig_params['f1'][6],fontsize=fig_params['f0'][0]+2,y=1.02)
        ax.xaxis.set_major_locator(matplotlib.ticker.MultipleLocator(10))
        ax.yaxis.set_major_locator(matplotlib.ticker.MultipleLocator(10))
    
        ax.tick_params(which='both',labelsize=fig_params['f0'][3])
    
        if plot_cbar:
        
            axpos = ax.get_position()
            height=axpos.height*cbar_coords[2]
            width=axpos.width*cbar_coords[3]
        
            cax=fig.add_axes([cbar_coords[0],cbar_coords[1],width,height])
            m = matplotlib.cm.ScalarMappable(cmap=cmap, norm=normed)
            m.set_array([])
            cb=plt.colorbar(m,cax=cax,label='')

            cb.ax.tick_params(which='both',labelsize=fig_params['f0'][3]-1)
            ax = cb.ax
            text = ax.yaxis.label
            font = matplotlib.font_manager.FontProperties(size=fig_params['f0'][3]-1)
            text.set_font_properties(font)


