import numpy as np
np.random.seed(2026)

import os, sys
from six.moves import range, zip, map, filter

from rootpy.plotting import Hist, Hist2D, Graph, Efficiency
from rootpy.tree import Tree, TreeChain, TreeModel, FloatCol, IntCol, ShortCol
from rootpy.io import root_open
#from rootpy.memory.keepalive import keepalive
from ROOT import gROOT, TH1
gROOT.SetBatch(True)
TH1.AddDirectory(False)

import logging
mpl_logger = logging.getLogger('matplotlib')
mpl_logger.setLevel(logging.WARNING)


# ______________________________________________________________________________
# Utilities

# Enums
kDT, kCSC, kRPC, kGEM, kME0 = 0, 1, 2, 3, 4

# Globals
eta_bins = (0.8, 1.24, 1.56, 1.7, 1.8, 1.98, 2.16, 2.4)
eta_bins = eta_bins[::-1]
#pt_bins = (-0.5, -0.365, -0.26, -0.155, -0.07, 0.07, 0.155, 0.26, 0.365, 0.5)
pt_bins = (-0.49349323, -0.38373062, -0.28128058, -0.18467896, -0.07760702, 0.07760702, 0.18467896, 0.28128058, 0.38373062, 0.49349323)
pt_bins_omtf = (-0.25, -0.2, -0.15, -0.10, -0.05, 0.05, 0.10, 0.15, 0.20, 0.25)  # starts from 4 GeV
nlayers = 16  # 5 (CSC) + 4 (RPC) + 3 (GEM) + 4 (DT)
#superstrip_size = 32  # 'quadstrip' unit (4 * 8)

assert(len(eta_bins) == 7+1)
assert(len(pt_bins) == 9+1)

# Functions
def root_sum_square(x, y):
  return np.sqrt(x*x + y*y)

def delta_phi(lhs, rhs):  # in radians
  rad = lhs - rhs
  while rad <  -np.pi:  rad += np.pi*2
  while rad >= +np.pi:  rad -= np.pi*2
  return rad

def delta_theta(lhs, rhs):  # in radians
  rad = lhs - rhs
  return rad

def range_phi_deg(deg):
  while deg <  -180.:
    deg += 360.
  while deg >= +180.:
    deg -= 360.
  return deg

def calc_phi_loc_deg_from_glob(glob, sector):
  # glob in deg, sector [1-6]
  glob = range_phi_deg(glob)
  loc = glob - 15. - (60. * (sector-1))
  return loc

def calc_phi_loc_int(glob, sector):
  # glob in deg, sector [1-6]
  loc = calc_phi_loc_deg_from_glob(glob, sector)
  if (loc + 22.) < 0.:
    loc += 360.
  loc = (loc + 22.) * 60.
  phi_int = int(round(loc))
  return phi_int

def calc_phi_loc_deg(bits):
  loc = float(bits)/60. - 22.
  return loc

def calc_phi_glob_deg(loc, sector):
  # loc in deg, sector [1-6]
  glob = loc + 15. + (60. * (sector-1))
  if glob >= 180.:
    glob -= 360.
  return glob

def calc_theta_int(theta, endcap):
  # theta in deg, endcap [-1,+1]
  if endcap == -1:
    theta = 180. - theta
  theta = (theta - 8.5) * 128./(45.0-8.5)
  theta_int = int(round(theta))
  return theta_int

def calc_theta_rad_from_eta(eta):
  theta = np.arctan2(1.0, np.sinh(eta))
  return theta

def calc_theta_deg_from_eta(eta):
  return np.rad2deg(calc_theta_rad_from_eta(eta))

def calc_theta_deg_from_int(theta_int):
  theta_deg = float(theta_int) * (45.0-8.5)/128. + 8.5;
  return theta_deg

def calc_eta_from_theta_deg(theta_deg, endcap):
  # theta in deg, endcap [-1,+1]
  theta_rad = np.deg2rad(theta_deg)
  eta = -1. * np.log(np.tan(theta_rad/2.))
  if endcap == -1:
    eta = -eta
  return eta

def extrapolate_to_emtf(phi, invpt, eta):  # phi in radians
  # 1.204 is the magic constant at eta of 1.9
  eta_sf = np.sinh(1.9) / np.sinh(np.abs(eta))
  return phi - 1.204 * invpt * eta_sf

def find_sector(phi):  # phi in radians
  dphi = delta_phi(phi, np.pi/12)  # sector 1 starts at 15 deg
  dphi = int(np.floor(dphi/(np.pi/3)))  # divide by 60 deg
  if dphi < 0:
    sector = 7 + dphi
  else:
    sector = 1 + dphi
  return sector

def find_endcap(eta):
  endcap = +1 if eta >= 0. else -1
  return endcap

def find_endsec(endcap, sector):
  endsec = (sector - 1) if endcap == 1 else (sector - 1 + 6)
  return endsec

def find_pt_bin(pt):
  ipt = np.digitize((pt,), pt_bins[1:])[0]  # skip lowest edge
  ipt = np.clip(ipt, 0, len(pt_bins)-2)
  return ipt

def find_pt_bin_omtf(pt):
  ipt = np.digitize((pt,), pt_bins_omtf[1:])[0]  # skip lowest edge
  ipt = np.clip(ipt, 0, len(pt_bins_omtf)-2)
  return ipt

def find_eta_bin(eta):
  ieta = np.digitize((np.abs(eta),), eta_bins[1:])[0]  # skip lowest edge
  ieta = np.clip(ieta, 0, len(eta_bins)-2)
  return ieta

def find_pattern_x(emtf_phi):
  return (emtf_phi+16)//32  # divide by 'quadstrip' unit (4 * 8)


# ______________________________________________________________________________
# Long functions

# Decide EMTF hit layer number
class EMTFLayer(object):
  def __init__(self):
    lut = np.zeros((5,5,5), dtype=np.int32) - 99  # (type, station, ring) -> layer
    lut[1,1,4] = 0  # ME1/1a
    lut[1,1,1] = 0  # ME1/1b
    lut[1,1,2] = 1  # ME1/2
    lut[1,1,3] = 1  # ME1/3
    lut[1,2,1] = 2  # ME2/1
    lut[1,2,2] = 2  # ME2/2
    lut[1,3,1] = 3  # ME3/1
    lut[1,3,2] = 3  # ME3/2
    lut[1,4,1] = 4  # ME4/1
    lut[1,4,2] = 4  # ME4/2
    lut[2,1,2] = 5  # RE1/2
    lut[2,1,3] = 5  # RE1/3
    lut[2,2,2] = 6  # RE2/2
    lut[2,2,3] = 6  # RE2/3
    lut[2,3,1] = 7  # RE3/1
    lut[2,3,2] = 7  # RE3/2
    lut[2,3,3] = 7  # RE3/3
    lut[2,4,1] = 8  # RE4/1
    lut[2,4,2] = 8  # RE4/2
    lut[2,4,3] = 8  # RE4/3
    lut[3,1,1] = 9  # GE1/1
    lut[3,2,1] = 10 # GE2/1
    lut[4,1,1] = 11 # ME0
    lut[0,1,1] = 12 # MB1
    lut[0,2,1] = 13 # MB2
    lut[0,3,1] = 14 # MB3
    lut[0,4,1] = 15 # MB4
    self.lut = lut

  def __call__(self, hit):
    index = (hit.type, hit.station, hit.ring)
    entry = self.lut[index]
    return entry

# Decide EMTF hit zones
class EMTFZone(object):
  def __init__(self):
    lut = np.zeros((5,5,5,7,2), dtype=np.int32) - 99  # (type, station, ring) -> [zone] x [min_theta,max_theta]
    lut[1,1,4][0] = 4,17    # ME1/1a
    lut[1,1,4][1] = 16,25   # ME1/1a
    lut[1,1,4][2] = 24,36   # ME1/1a
    lut[1,1,4][3] = 34,43   # ME1/1a
    lut[1,1,4][4] = 41,53   # ME1/1a
    lut[1,1,1][0] = 4,17    # ME1/1b
    lut[1,1,1][1] = 16,25   # ME1/1b
    lut[1,1,1][2] = 24,36   # ME1/1b
    lut[1,1,1][3] = 34,43   # ME1/1b
    lut[1,1,1][4] = 41,53   # ME1/1b
    lut[1,1,2][4] = 46,54   # ME1/2
    lut[1,1,2][5] = 52,88   # ME1/2
    lut[1,1,2][6] = 78,88   # ME1/2
    lut[1,1,3][6] = 98,125  # ME1/3
    #
    lut[1,2,1][0] = 4,17    # ME2/1
    lut[1,2,1][1] = 16,25   # ME2/1
    lut[1,2,1][2] = 24,36   # ME2/1
    lut[1,2,1][3] = 34,43   # ME2/1
    lut[1,2,1][4] = 41,49   # ME2/1
    lut[1,2,2][5] = 53,90   # ME2/2
    lut[1,2,2][6] = 77,111  # ME2/2
    #
    lut[1,3,1][0] = 4,17    # ME3/1
    lut[1,3,1][1] = 16,25   # ME3/1
    lut[1,3,1][2] = 24,36   # ME3/1
    lut[1,3,1][3] = 34,40   # ME3/1
    lut[1,3,2][4] = 44,54   # ME3/2
    lut[1,3,2][5] = 52,90   # ME3/2
    lut[1,3,2][6] = 76,96   # ME3/2
    #
    lut[1,4,1][0] = 4,17    # ME4/1
    lut[1,4,1][1] = 16,25   # ME4/1
    lut[1,4,1][2] = 24,35   # ME4/1
    lut[1,4,2][3] = 38,43   # ME4/2
    lut[1,4,2][4] = 41,54   # ME4/2
    lut[1,4,2][5] = 52,90   # ME4/2
    #
    lut[2,1,2][5] = 52,84   # RE1/2
    lut[2,1,3][6] = 80,120  # RE1/3
    lut[2,2,2][5] = 56,88   # RE2/2
    lut[2,2,3][6] = 76,112  # RE2/3
    lut[2,3,1][0] = 4,17    # RE3/1
    lut[2,3,1][1] = 16,25   # RE3/1
    lut[2,3,1][2] = 24,36   # RE3/1
    lut[2,3,2][3] = 40,40   # RE3/2
    lut[2,3,2][4] = 40,52   # RE3/2
    lut[2,3,2][5] = 48,84   # RE3/2
    lut[2,3,3][3] = 40,40   # RE3/3
    lut[2,3,3][4] = 40,52   # RE3/3
    lut[2,3,3][5] = 48,84   # RE3/3
    lut[2,3,3][6] = 80,92   # RE3/3
    lut[2,4,1][0] = 4,17    # RE4/1
    lut[2,4,1][1] = 16,25   # RE4/1
    lut[2,4,1][2] = 24,31   # RE4/1
    lut[2,4,2][3] = 36,44   # RE4/2
    lut[2,4,2][4] = 44,52   # RE4/2
    lut[2,4,2][5] = 52,84   # RE4/2
    lut[2,4,3][3] = 36,44   # RE4/3
    lut[2,4,3][4] = 44,52   # RE4/3
    lut[2,4,3][5] = 52,84   # RE4/3
    #
    lut[3,1,1][1] = 16,26   # GE1/1
    lut[3,1,1][2] = 24,37   # GE1/1
    lut[3,1,1][3] = 35,45   # GE1/1
    lut[3,1,1][4] = 40,52   # GE1/1
    lut[3,2,1][0] = 7,19    # GE2/1
    lut[3,2,1][1] = 18,24   # GE2/1
    lut[3,2,1][2] = 23,36   # GE2/1
    lut[3,2,1][3] = 34,45   # GE2/1
    lut[3,2,1][4] = 40,46   # GE2/1
    #
    lut[4,1,1][0] = 4,17    # ME0
    lut[4,1,1][1] = 16,23   # ME0
    #
    lut[0,1,1][6] = 92,130  # MB1
    lut[0,2,1][6] = 108,138 # MB2
    lut[0,3,1][6] = 126,144 # MB3
    self.lut = lut

  def __call__(self, hit):
    index = (hit.type, hit.station, hit.ring)
    entry = self.lut[index]
    answer = (entry[:,0] <= hit.emtf_theta) & (hit.emtf_theta <= entry[:,1])
    zones = np.nonzero(answer)
    if isinstance(zones, tuple):
      zones = zones[0]
    return zones

# Decide EMTF hit bend
class EMTFBend(object):
  def __call__(self, hit):
    emtf_bend = np.int32(hit.bend)
    if hit.type == kCSC:
      # Special case for ME1/1a:
      # rescale the bend to the same scale as ME1/1b
      if hit.station == 1 and hit.ring == 4:
        emtf_bend = np.round(emtf_bend.astype(np.float32) * 0.026331/0.014264).astype(np.int32)
        emtf_bend = np.clip(emtf_bend, -32, 31)
      emtf_bend *= hit.endcap
      emtf_bend /= 2  # from 1/32-strip unit to 1/16-strip unit
    elif hit.type == kGEM:
      emtf_bend *= hit.endcap
    elif hit.type == kME0:
      emtf_bend = np.clip(emtf_bend, -64, 63)  # currently in 1/2-strip unit
    elif hit.type == kDT:
      if hit.quality >= 4:
        emtf_bend = np.clip(emtf_bend, -512, 511)
      else:
        #emtf_bend = 0
        emtf_bend = np.clip(emtf_bend, -512, 511)
    else:  # kRPC
      emtf_bend = 0
    return emtf_bend

# Decide EMTF hit bend (old version)
class EMTFOldBend(object):
  def __init__(self):
    self.lut = np.array([5, -5, 4, -4, 3, -3, 2, -2, 1, -1, 0], dtype=np.int32)

  def __call__(self, hit):
    if hit.type == kCSC:
      clct = int(hit.pattern)
      bend = self.lut[clct]
      bend *= hit.endcap
    elif hit.type == kGEM:
      bend = hit.bend
      bend *= hit.endcap
    elif hit.type == kME0:
      bend = hit.bend
    elif hit.type == kDT:
      bend = hit.bend
    else:  # kRPC
      bend = 0
    return bend

# Decide EMTF hit z-position
class EMTFZee(object):
  def __init__(self):
    self.lut = np.array([599.0, 696.8, 827.1, 937.5, 1027, 708.7, 790.9, 968.8, 1060, 566.4, 794.8, 539.3, 0, 0, 0, 0], dtype=np.float32)
    assert(self.lut.shape[0] == nlayers)

  def __call__(self, hit):
    return self.lut[hit.emtf_layer]

# Decide EMTF hit phi (integer unit)
class EMTFPhi(object):
  def __call__(self, hit):
    emtf_phi = np.int32(hit.emtf_phi)
    if hit.type == kCSC:
      if hit.station == 1:
        if hit.ring == 1:
          bend_corr_lut = (-2.0832, 2.0497)  # ME1/1b (r,f)
        elif hit.ring == 4:
          bend_corr_lut = (-2.4640, 2.3886)  # ME1/1a (r,f)
        elif hit.ring == 2:
          bend_corr_lut = (-1.3774, 1.2447)  # ME1/2 (r,f)
        else:
          bend_corr_lut = (-0, 0)            # ME1/3 (r,f): no correction
        bend_corr = bend_corr_lut[int(hit.fr)] * hit.bend
        bend_corr = bend_corr if hit.endcap == 1 else (bend_corr * -1)
        bend_corr = int(round(bend_corr))
        emtf_phi = emtf_phi + bend_corr
      else:
        pass
    else:
      pass
    return emtf_phi

# Decide EMTF hit theta (integer unit)
class EMTFTheta(object):
  def __call__(self, hit):
    emtf_theta = np.int32(hit.emtf_theta)
    if hit.type == kDT:
      if hit.wire == -1:
        if hit.station == 1:
          emtf_theta = 112
        elif hit.station == 2:
          emtf_theta = 122
        elif hit.station == 3:
          emtf_theta = 131
      else:
        pass
    else:
      pass
    return emtf_theta

# Decide EMTF hit quality
class EMTFQuality(object):
  def __call__(self, hit):
    emtf_quality = np.int32(hit.quality)
    if hit.type == kCSC or hit.type == kME0:
      # front chamber -> +1
      # rear chamber  -> -1
      if int(hit.fr) == 1:
        emtf_quality = emtf_quality * +1
      else:
        emtf_quality = emtf_quality * -1
    return emtf_quality

# Decide EMTF hit time (integer unit)
class EMTFTime(object):
  def __call__(self, hit):
    #emtf_time = hit.time
    emtf_time = np.int32(hit.bx)
    return emtf_time

# Decide EMTF hit layer partner (to make pairs and calculate deflection angles)
class EMTFLayerPartner(object):
  def __init__(self):
    self.lut = np.array([2, 2, 0, 0, 0, 0, 2, 3, 4, 0, 2, 0, 0, 0, 0, 0], dtype=np.int32)
    assert(self.lut.shape[0] == nlayers)

  def __call__(self, emtf_layer, zone):
    partner = self.lut[emtf_layer]
    if zone >= 5:  # zones 5,6, use ME1/2
      if partner == 0:
        partner = 1
    return partner

# Decide EMTF road quality (by pT)
class EMTFRoadQuality(object):
  def __init__(self):
    self.best_ipt = find_pt_bin(0.)

  def __call__(self, ipt):
    return self.best_ipt - np.abs(ipt - self.best_ipt)

# Decide EMTF road sort code
class EMTFRoadSortCode(object):
  def __init__(self):
    # 9    8      7      6    5      4    3    2..0
    #      ME1/1  ME1/2  ME2         ME3  ME4  qual
    #                         RE1&2  RE3  RE4
    # ME0         GE1/1       GE2/1
    # MB1  MB2                MB3&4
    self.lut = np.array([8,7,6,4,3,5,5,4,3,7,5,9,9,8,5,5], dtype=np.int32)
    assert(self.lut.shape[0] == nlayers)

  def __call__(self, mode, qual, hits):
    code = np.int32(0)
    for hit in hits:
      hit_lay = hit.emtf_layer
      mlayer = self.lut[hit_lay]
      code |= (1 << mlayer)
    code |= qual
    return code

find_emtf_layer = EMTFLayer()
find_emtf_zones = EMTFZone()
find_emtf_bend = EMTFBend()
find_emtf_old_bend = EMTFOldBend()
find_emtf_zee = EMTFZee()
find_emtf_phi = EMTFPhi()
find_emtf_theta = EMTFTheta()
find_emtf_quality = EMTFQuality()
find_emtf_time = EMTFTime()
find_emtf_layer_partner = EMTFLayerPartner()
find_emtf_road_quality = EMTFRoadQuality()
find_emtf_road_sort_code = EMTFRoadSortCode()

def is_emtf_singlemu(mode):
  return mode in (11,13,14,15)

def is_emtf_doublemu(mode):
  return mode in (7,10,12) + (11,13,14,15)

def is_emtf_muopen(mode):
  return mode in (3,5,6,9) + (7,10,12) + (11,13,14,15)

def is_emtf_singlehit(mode):
  return bool(mode & (1 << 3))

def is_emtf_singlehit_me2(mode):
  return bool(mode & (1 << 2))

# Decide EMTF legit hit
def is_emtf_legit_hit(hit):
  def check_bx(hit):
    if hit.type == kCSC:
      return hit.bx in (-1,0)
    elif hit.type == kDT:
      return hit.bx in (-1,0)
    else:
      return hit.bx == 0
  def check_emtf_phi(hit):
    if hit.type == kME0:
      return hit.emtf_phi > 0
    elif hit.type == kDT:
      return hit.emtf_phi > 0
    else:
      return True
  return check_bx(hit) and check_emtf_phi(hit)

def is_valid_for_run2(hit):
  is_csc = (hit.type == kCSC)
  is_rpc = (hit.type == kRPC)
  is_irpc = (hit.type == kRPC) and ((hit.station == 3 or hit.station == 4) and hit.ring == 1)
  is_omtf = (hit.type == kRPC) and ((hit.station == 1 or hit.station == 2) and hit.ring == 3)
  return (is_csc or (is_rpc and not is_irpc and not is_omtf))

# From https://stackoverflow.com/a/31539746
def weighted_percentile(data, percents, weights=None):
  ''' percents in units of 1%
  weights specifies the frequency (count) of data.
  '''
  if weights is None:
    return np.percentile(data, percents)
  ind=np.argsort(data)
  d=data[ind]
  w=weights[ind]
  p=1.*w.cumsum()/w.sum()*100
  y=np.interp(percents, p, d)
  return y


# ______________________________________________________________________________
# Data Formats

PARTICLE_NVARS = 5

class Particle(object):
  def __init__(self, pt, eta, phi, q, vx, vy, vz):
    self.pt = pt
    self.eta = eta
    self.phi = phi
    self.q = q
    self.vx = vx
    self.vy = vy
    self.vz = vz

  def to_parameters(self):
    # Convert into an entry in a numpy array
    # (q/pT, phi, eta, dxy, dz)
    parameters = np.array((np.true_divide(self.q, self.pt), self.phi, self.eta, root_sum_square(self.vx, self.vy), self.vz), dtype=np.float32)
    return parameters

class PatternBank(object):
  def __init__(self, bankfile):
    with np.load(bankfile) as data:
      patterns_phi = data['patterns_phi']
      #patterns_theta = data['patterns_theta']
      patterns_match = data['patterns_match']
    self.x_array = patterns_phi
    #self.y_array = patterns_theta
    self.z_array = patterns_match
    assert(self.x_array.dtype == np.int32)
    #assert(self.y_array.dtype == np.int32)
    assert(self.z_array.dtype == np.int32)
    assert(self.x_array.shape == (len(pt_bins)-1, len(eta_bins)-1, nlayers, 3))
    #assert(self.y_array.shape == (len(pt_bins)-1, len(eta_bins)-1, nlayers, 3))
    assert(self.z_array.shape == (len(pt_bins)-1, len(eta_bins)-1, nlayers, 3))

class Hit(object):
  def __init__(self, _id, emtf_layer, emtf_phi, emtf_theta, emtf_bend,
               emtf_quality, emtf_time, old_emtf_phi, old_emtf_bend,
               extra_emtf_theta, sim_tp):
    self.id = _id  # (_type, station, ring, sector, fr, bx)
    self.emtf_layer = emtf_layer
    self.emtf_phi = emtf_phi
    self.emtf_theta = emtf_theta
    self.emtf_bend = emtf_bend
    self.emtf_quality = emtf_quality
    self.emtf_time = emtf_time
    self.old_emtf_phi = old_emtf_phi
    self.old_emtf_bend = old_emtf_bend
    self.extra_emtf_theta = extra_emtf_theta
    self.sim_tp = sim_tp

  def get_ring(self):
    return self.id[2]
  def get_sector(self):
    return self.id[3]
  def get_fr(self):
    return self.id[4]
  def get_bx(self):
    return self.id[5]

ROAD_LAYER_NVARS = 10  # each layer in the road carries 10 variables
ROAD_LAYER_NVARS_P1 = ROAD_LAYER_NVARS + 1  # plus layer mask
ROAD_INFO_NVARS = 3

class Road(object):
  def __init__(self, _id, hits, mode, quality, sort_code, theta_median):
    self.id = _id  # (endcap, sector, ipt, ieta, iphi)
    self.hits = hits
    self.mode = mode
    self.quality = quality
    self.sort_code = sort_code
    self.theta_median = theta_median

  def to_variables(self):
    # Convert into an entry in a numpy array
    # At the moment, each entry carries (nlayers * 11) + 3 values
    amap = {}
    (endcap, sector, ipt, ieta, iphi) = self.id
    road_info = (ipt, ieta, iphi)
    #np.random.shuffle(self.hits)  # randomize the order
    for hit in self.hits:
      hit_lay = hit.emtf_layer
      if hit_lay not in amap:
        amap[hit_lay] = hit
    arr = np.zeros((ROAD_LAYER_NVARS_P1 * nlayers) + ROAD_INFO_NVARS, dtype=np.float32)
    arr[0*nlayers:ROAD_LAYER_NVARS*nlayers] = np.nan                # variables (n=nlayers * 10)
    arr[ROAD_LAYER_NVARS*nlayers:ROAD_LAYER_NVARS_P1*nlayers] = 1.0 # mask      (n=nlayers * 1)
    arr[ROAD_LAYER_NVARS_P1*nlayers:] = road_info                   # road info (n=3)
    for lay, hit in amap.iteritems():
      ind = [i*nlayers + lay for i in xrange(ROAD_LAYER_NVARS)]
      arr[ind] = (hit.emtf_phi, hit.emtf_theta, hit.emtf_bend, hit.emtf_quality, hit.emtf_time,
                  hit.get_ring(), hit.get_fr(), hit.old_emtf_phi, hit.old_emtf_bend, hit.extra_emtf_theta)
      ind = (ROAD_LAYER_NVARS*nlayers + lay)
      arr[ind] = 0.0  # unmask
    return arr

class Track(object):
  def __init__(self, _id, hits, mode, zone, xml_pt, pt, q, emtf_phi, emtf_theta, ndof, chi2):
    assert(pt > 0.)
    self.id = _id  # (endcap, sector)
    self.hits = hits
    self.mode = mode
    self.zone = zone
    self.xml_pt = xml_pt
    self.pt = pt
    self.q = q
    self.emtf_phi = emtf_phi
    self.emtf_theta = emtf_theta
    self.ndof = ndof
    self.chi2 = chi2
    self.phi = calc_phi_glob_deg(calc_phi_loc_deg(emtf_phi), _id[1])
    self.eta = calc_eta_from_theta_deg(calc_theta_deg_from_int(emtf_theta), _id[0])

# Save particle list as a numpy array
def particles_to_parameters(particles):
  parameters = np.zeros((len(particles), PARTICLE_NVARS), dtype=np.float32)
  for i, part in enumerate(particles):
    parameters[i] = part.to_parameters()
  return parameters

# Save road list as a numpy array
def roads_to_variables(roads):
  variables = np.zeros((len(roads), (ROAD_LAYER_NVARS_P1 * nlayers) + ROAD_INFO_NVARS), dtype=np.float32)
  for i, road in enumerate(roads):
    variables[i] = road.to_variables()
  return variables


# ______________________________________________________________________________
# Modules

PATTERN_X_CENTRAL = 23  # pattern bin number 23 is the central
PATTERN_X_SEARCH_MIN = 33
PATTERN_X_SEARCH_MAX = 154-10

# Pattern recognition module
class PatternRecognition(object):
  def __init__(self, bank, omtf_input=False, run2_input=False):
    self.bank = bank
    self.cache = dict()  # cache for pattern results
    self.omtf_input = omtf_input
    self.run2_input = run2_input

  def _create_road_hit(self, hit):
    hit_id = (hit.type, hit.station, hit.ring, hit.endsec, hit.fr, hit.bx)
    emtf_bend = find_emtf_bend(hit)
    emtf_quality = find_emtf_quality(hit)
    emtf_time = find_emtf_time(hit)
    old_emtf_bend = find_emtf_old_bend(hit)
    extra_emtf_theta = 0  #FIXME
    sim_tp = (hit.sim_tp1 == 0 and hit.sim_tp2 == 0)
    myhit = Hit(hit_id, hit.lay, hit.emtf_phi, hit.emtf_theta, emtf_bend,
                emtf_quality, emtf_time, hit.old_emtf_phi, old_emtf_bend,
                extra_emtf_theta, sim_tp)
    return myhit

  def _apply_patterns_in_zone(self, zone, hit_lay):
    result = self.cache.get((zone, hit_lay), None)
    if result is not None:
      return result

    # Retrieve patterns with (ipt, ieta, lay, pattern)
    patterns_x0 = self.bank.x_array[:, zone, hit_lay, 0, np.newaxis]
    patterns_x1 = self.bank.x_array[:, zone, hit_lay, 2, np.newaxis]
    patterns_iphi = np.arange(-PATTERN_X_CENTRAL, PATTERN_X_CENTRAL+1, dtype=np.int32)
    mask = (patterns_x0 <= patterns_iphi) & (patterns_iphi <= patterns_x1)
    result = np.transpose(np.nonzero(mask))
    self.cache[(zone, hit_lay)] = result
    return result

  def _apply_patterns(self, endcap, sector, sector_hits):
    amap = {}  # road_id -> road_hits

    # Loop over hits
    for ihit, hit in enumerate(sector_hits):
      hit_x = find_pattern_x(hit.emtf_phi)
      #hit_y = hit.emtf_theta
      hit_lay = hit.lay
      hit_zones = hit.zones

      myhit = None

      # Loop over the zones that the hit is belong to
      for zone in hit_zones:
        if self.omtf_input:
          if zone != 6:  # only zone 6
            continue
        else:
          if zone == 6:  # ignore zone 6
            continue

        result = self._apply_patterns_in_zone(zone, hit_lay)

        for index in result:
          ipt, iphi = index
          iphi = hit_x - (iphi - PATTERN_X_CENTRAL)  # iphi 0 starts at -23
          ieta = zone

          # Full range is 0 <= iphi <= 154. but a reduced range is sufficient (27% saving on patterns)
          if PATTERN_X_SEARCH_MIN <= iphi <= PATTERN_X_SEARCH_MAX:
            # Create and associate 'myhit' to road ids
            if myhit is None:
              myhit = self._create_road_hit(hit)
            road_id = (endcap, sector, ipt, ieta, iphi)
            amap.setdefault(road_id, []).append(myhit)  # append hit to road

    # Create roads
    roads = []
    for road_id, road_hits in amap.iteritems():
      (endcap, sector, ipt, ieta, iphi) = road_id
      road_mode = 0
      road_mode_csc = 0
      road_mode_me0 = 0
      road_mode_mb1 = 0
      road_mode_mb2 = 0
      road_mode_me13 = 0
      road_mode_me22 = 0
      road_mode_omtf = 0
      tmp_road_hits = []
      tmp_thetas = []

      for hit in road_hits:
        (_type, station, ring, endsec, fr, bx) = hit.id
        road_mode |= (1 << (4 - station))

        if _type == kCSC or _type == kME0:
          road_mode_csc |= (1 << (4 - station))

        if _type == kME0:
          road_mode_me0 |= (1 << 1)
        elif _type == kCSC and station == 1 and (ring == 1 or ring == 4):
          road_mode_me0 |= (1 << 0)

        if _type == kDT and station == 1:
          road_mode_mb1 |= (1 << 1)
        elif _type == kDT and station >= 2:
          road_mode_mb1 |= (1 << 0)
        elif _type == kCSC and station >= 1 and (ring == 2 or ring == 3):
          road_mode_mb1 |= (1 << 0)
        elif _type == kRPC and station >= 1 and (ring == 2 or ring == 3):
          road_mode_mb1 |= (1 << 0)

        if _type == kDT and station == 2:
          road_mode_mb2 |= (1 << 1)
        elif _type == kDT and station >= 3:
          road_mode_mb2 |= (1 << 0)
        elif _type == kCSC and station >= 1 and (ring == 2 or ring == 3):
          road_mode_mb2 |= (1 << 0)
        elif _type == kRPC and station >= 1 and (ring == 2 or ring == 3):
          road_mode_mb2 |= (1 << 0)

        if _type == kCSC and station == 1 and (ring == 2 or ring == 3):
          road_mode_me13 |= (1 << 1)
        elif _type == kCSC and station >= 2 and (ring == 2 or ring == 3):
          road_mode_me13 |= (1 << 0)
        elif _type == kRPC and station == 1 and (ring == 2 or ring == 3):
          road_mode_me13 |= (1 << 1)
        elif _type == kRPC and station >= 2 and (ring == 2 or ring == 3):
          road_mode_me13 |= (1 << 0)

        #if _type == kCSC and station == 2 and (ring == 2 or ring == 3):
        #  road_mode_me22 |= (1 << 1)
        #elif _type == kCSC and station >= 3 and (ring == 2 or ring == 3):
        #  road_mode_me22 |= (1 << 0)
        #elif _type == kRPC and station == 2 and (ring == 2 or ring == 3):
        #  road_mode_me22 |= (1 << 1)
        #elif _type == kRPC and station >= 3 and (ring == 2 or ring == 3):
        #  road_mode_me22 |= (1 << 0)

        road_mode_omtf = np.max((road_mode_mb1, road_mode_mb2, road_mode_me13))

        tmp_road_hits.append(hit)

        tmp_thetas.append(hit.emtf_theta)
        continue  # end loop over road_hits

      # Apply SingleMu requirement
      # + (zones 0,1) any road with ME0 and ME1
      # + (zone 6) any road with MB1+MB2, MB1+MB3, MB1+ME1/3, MB1+ME2/2, MB2+MB3, MB2+ME1/3, MB2+ME2/2, ME1/3+ME2/2
      if ((is_emtf_singlemu(road_mode) and is_emtf_muopen(road_mode_csc)) or \
          (ieta in (0,1) and road_mode_me0 == 3) or \
          (ieta in (6,) and road_mode_omtf == 3)):
        road_quality = find_emtf_road_quality(ipt)
        road_sort_code = find_emtf_road_sort_code(road_mode, road_quality, tmp_road_hits)
        tmp_theta = np.median(tmp_thetas, overwrite_input=True)

        myroad = Road(road_id, tmp_road_hits, road_mode, road_quality, road_sort_code, tmp_theta)
        roads.append(myroad)
      continue  # end loop over map of road_id -> road_hits
    return roads

  def run(self, hits):
    roads = []

    # Split by sector
    sector_mode_array = np.zeros((12,), dtype=np.int32)
    sector_hits_array = np.empty((12,), dtype=np.object)
    for ind in np.ndindex(sector_hits_array.shape):
      sector_hits_array[ind] = []

    legit_hits = filter(is_emtf_legit_hit, hits)

    for ihit, hit in enumerate(legit_hits):
      hit.endsec = find_endsec(hit.endcap, hit.sector)
      hit.lay = find_emtf_layer(hit)
      assert(hit.lay != -99)

      if hit.type == kCSC:
        sector_mode_array[hit.endsec] |= (1 << (4 - hit.station))
      elif hit.type == kME0:
        sector_mode_array[hit.endsec] |= (1 << (4 - 1))
      elif hit.type == kDT:
        sector_mode_array[hit.endsec] |= (1 << (4 - 1))
      sector_hits_array[hit.endsec].append(hit)

    # Loop over sector processors
    for endcap in (-1, +1):
      for sector in (1, 2, 3, 4, 5, 6):
        endsec = find_endsec(endcap, sector)
        sector_mode = sector_mode_array[endsec]
        sector_hits = sector_hits_array[endsec]

        # Provide early exit if fail MuOpen and no hit in stations 1&2 (check CSC, ME0, DT)
        if not is_emtf_muopen(sector_mode) and \
            not is_emtf_singlehit(sector_mode) and \
            not is_emtf_singlehit_me2(sector_mode):
          continue

        # Remove all RPC hits
        #sector_hits = [hit for hit in sector_hits if hit.type != kRPC]

        # Remove all non-Run 2 hits
        if self.run2_input:
          sector_hits = list(filter(is_valid_for_run2, sector_hits))

        # Loop over sector hits
        for ihit, hit in enumerate(sector_hits):
          hit.old_emtf_phi = hit.emtf_phi
          hit.emtf_phi = find_emtf_phi(hit)
          hit.emtf_theta = find_emtf_theta(hit)
          hit.zones = find_emtf_zones(hit)

        # Apply patterns to the sector hits
        sector_roads = self._apply_patterns(endcap, sector, sector_hits)
        roads += sector_roads
    return roads


# Road cleaning module
class RoadCleaning(object):
  def __init__(self):
    pass

  # https://stackoverflow.com/a/30396816
  def _iter_from_middle(self, lst):
    try:
      middle = len(lst)//2
      yield lst[middle]
      for shift in xrange(1, middle+1):
        # order is important!
        yield lst[middle - shift]
        yield lst[middle + shift]
    except IndexError: # occures on lst[len(lst)] or for empty list
      raise StopIteration

  def _groupby(self, data):
    def is_adjacent(prev, curr, length):
      # adjacent if (x,y,z) == (x,y,z+length)
      return prev[:-1] == curr[:-1] and (prev[-1] + length) == curr[-1]

    if data:
      data.sort()
      myiter = iter(data)
      prev = curr = next(myiter)
      # Iterate over data
      while True:
        group = []
        stop = False
        # Iterate until the next value is different
        while is_adjacent(prev, curr, len(group)):
          try:
            group.append(curr)
            curr = next(myiter)
          except StopIteration:
            stop = True
            break
        # Output group
        yield group
        prev = curr
        if stop:
          return

  def _sortby(self, clean_roads, groupinfo):
    def select_bx_zero(road):
      bx_counter1 = 0  # count hits with BX <= -1
      bx_counter2 = 0  # count hits with BX <= 0
      bx_counter3 = 0  # count hits with BX > 0
      layer_has_been_used = set()
      for hit in road.hits:
        if hit.emtf_layer not in layer_has_been_used:
          layer_has_been_used.add(hit.emtf_layer)
          if hit.get_bx() <= -1:
            bx_counter1 += 1
          if hit.get_bx() <= 0:
            bx_counter2 += 1
          if hit.get_bx() > 0:
            bx_counter3 += 1
      #trk_bx_zero = (bx_counter1 < 2 and bx_counter2 >= 2)
      trk_bx_zero = (bx_counter1 <= 2 and bx_counter2 >= 2 and bx_counter3 <= 1)
      return trk_bx_zero

    clean_roads = list(filter(select_bx_zero, clean_roads))

    if clean_roads:
      # Sort by 'sort code'
      clean_roads.sort(key=lambda road: road.sort_code, reverse=True)

      # Iterate over clean_roads
      for i, road in enumerate(clean_roads):
        keep = True
        gi = groupinfo[road.id]

        # No intersect between two ranges (x1, x2), (y1, y2): (x2 < y1) || (x1 > y2)
        # Intersect: !((x2 < y1) || (x1 > y2)) = (x2 >= y1) and (x1 <= y2)
        for j, road_to_check in enumerate(clean_roads[:i]):
          gj = groupinfo[road_to_check.id]
          _get_endsec = lambda x: x[:2]
          # Allow +/-2 due to extrapolation-to-EMTF error
          if (_get_endsec(road.id) == _get_endsec(road_to_check.id)) and (gi[1]+2 >= gj[0]) and (gi[0]-2 <= gj[1]):
            keep = False
            break

        if keep:
          # Do not share ME1/1, ME1/2, ME0, MB1, MB2
          for j, road_to_check in enumerate(clean_roads[:i]):
            hits_i = [(hit.emtf_layer, hit.emtf_phi) for hit in road.hits if hit.emtf_layer in (0,1,11,12,13)]
            hits_j = [(hit.emtf_layer, hit.emtf_phi) for hit in road_to_check.hits if hit.emtf_layer in (0,1,11,12,13)]
            if set(hits_i).intersection(hits_j):
              keep = False
              break

        if keep:
          yield road
      return

  def run(self, roads):
    # road_id = (endcap, sector, ipt, ieta, iphi)
    amap = {road.id : road for road in roads}

    # pick median in each iphi group
    clean_roads = []
    groupinfo = {}

    # Loop over road clusters
    for group in self._groupby(amap.keys()):

      # Loop over roads in road clusters, starting from middle
      for index in self._iter_from_middle(xrange(len(group))):
        road_id = group[index]
        road = amap[road_id]
        keep = True
        if (0 <= index-1) and road.sort_code < amap[group[index-1]].sort_code:
          keep = False
        if (index+1 < len(group)) and road.sort_code < amap[group[index+1]].sort_code:
          keep = False
        if keep:
          break

      _get_iphi = lambda x: x[4]
      g = (_get_iphi(group[0]), _get_iphi(group[-1]))  # first and last road_id's in the iphi group
      groupinfo[road_id] = g
      clean_roads.append(road)

    # sort the roads + kill the siblings
    sorted_clean_roads = []
    for road in self._sortby(clean_roads, groupinfo):
      sorted_clean_roads.append(road)
    return sorted_clean_roads


# Road slimming module
class RoadSlimming(object):
  def __init__(self, bank):
    self.bank = bank

  def run(self, roads):
    slim_roads = []

    for road in roads:
      ipt, ieta, iphi = road.id[2:]

      prim_match_lut = self.bank.z_array[ipt, ieta, :, 1]

      tmp_phi = (iphi * 32)  # multiply by 'quadstrip' unit (4 * 8)

      #_select_csc = lambda x: (x.emtf_layer <= 4)
      #tmp_thetas = [hit.emtf_theta for hit in road.hits if _select_csc(hit)]
      #tmp_theta = np.median(tmp_thetas, overwrite_input=True)
      tmp_theta = road.theta_median

      hits_array = np.empty((nlayers,), dtype=np.object)
      for ind in np.ndindex(hits_array.shape):
        hits_array[ind] = []

      best_phi_array = np.full((nlayers,), tmp_phi, dtype=np.int32)
      best_theta_array = np.full((nlayers,), tmp_theta, dtype=np.int32)

      # Put in the best estimate for the CSC stations
      best_estimate_me11 = tmp_phi + prim_match_lut[0]
      best_estimate_me12 = tmp_phi + prim_match_lut[1]
      if ieta >= 5:  # zones 5,6, use ME1/2
        best_estimate_me2 = best_estimate_me12 + prim_match_lut[2]
        best_estimate_me3 = best_estimate_me12 + prim_match_lut[3]
        best_estimate_me4 = best_estimate_me12 + prim_match_lut[4]
      else:
        best_estimate_me2 = best_estimate_me11 + prim_match_lut[2]
        best_estimate_me3 = best_estimate_me11 + prim_match_lut[3]
        best_estimate_me4 = best_estimate_me11 + prim_match_lut[4]
      best_phi_array[0:5] = (best_estimate_me11, best_estimate_me12, best_estimate_me2, best_estimate_me3, best_estimate_me4)

      for hit in road.hits:
        hit_lay = hit.emtf_layer
        hits_array[hit_lay].append(hit)

      # Assume going through ME1, ME2, ... in order
      for hit_lay in xrange(nlayers):
        mean_dphi = prim_match_lut[hit_lay]
        hit_lay_p = find_emtf_layer_partner(hit_lay, ieta)

        # Make pairs of (hit1, hit2)
        # Want to pick the best hit1, given the selection of hit2 and mean_dphi
        pairs = []
        if hits_array[hit_lay]:
          for hit1 in hits_array[hit_lay]:
            if hits_array[hit_lay_p]:
              for hit2 in hits_array[hit_lay_p]:
                dphi = abs((hit1.emtf_phi - hit2.emtf_phi) - mean_dphi)
                dtheta = abs(hit1.emtf_theta - tmp_theta)
                pairs.append((hit1, hit2, dphi, dtheta))
                #print hit1.emtf_phi, hit2.emtf_phi, abs((hit1.emtf_phi - hit2.emtf_phi)), dphi
                continue  # end loop over hit2
            else:
              dphi = abs((hit1.emtf_phi - best_phi_array[hit_lay_p]) - mean_dphi)
              dtheta = abs(hit1.emtf_theta - tmp_theta)
              pairs.append((hit1, hit1, dphi, dtheta))
            continue  # end loop over hit1

          # Find best pair, which is min (dtheta, dphi)
          best_pair = min(pairs, key=lambda x: (x[3], x[2]))
          best_hit = best_pair[0]
          hits_array[hit_lay] = [best_hit]
          best_phi_array[hit_lay] = best_hit.emtf_phi
          best_theta_array[hit_lay] = best_hit.emtf_theta

      slim_road_hits = []
      for hit_lay in xrange(nlayers):
        if hits_array[hit_lay]:
          assert(len(hits_array[hit_lay]) == 1)
          slim_road_hits.append(hits_array[hit_lay][0])

      slim_road = Road(road.id, slim_road_hits, road.mode, road.quality, road.sort_code, road.theta_median)
      slim_roads.append(slim_road)
    return slim_roads


# pT assignment module
class PtAssignment(object):
  def __init__(self, kerasfile, omtf_input=False, run2_input=False):
    (model_file, model_weights_file, model_omtf_file, model_omtf_weights_file) = kerasfile
    self.omtf_input = omtf_input
    self.run2_input = run2_input

    self.reg_pt_scale = 100.

    # Get encoders
    from nn_encode import Encoder
    from nn_encode_omtf import Encoder as EncoderOmtf

    # Load Keras models
    from nn_models import load_my_model, update_keras_custom_objects
    update_keras_custom_objects()

    # First model (EMTF mode)
    self.loaded_model = load_my_model(name=model_file, weights_name=model_weights_file)
    self.loaded_model.trainable = False
    assert not self.loaded_model.updates

    def create_encoder(x):
      nentries = x.shape[0]
      y = np.zeros((nentries, 1), dtype=np.float32)  # dummy
      encoder = Encoder(x, y, reg_pt_scale=self.reg_pt_scale)
      return encoder
    self.create_encoder = create_encoder

    # Second model (OMTF mode)
    self.loaded_model_omtf = load_my_model(name=model_omtf_file, weights_name=model_omtf_weights_file)
    self.loaded_model_omtf.trainable = False
    assert not self.loaded_model_omtf.updates

    def create_encoder_omtf(x):
      nentries = x.shape[0]
      y = np.zeros((nentries, 1), dtype=np.float32)  # dummy
      encoder = EncoderOmtf(x, y, reg_pt_scale=self.reg_pt_scale)
      return encoder
    self.create_encoder_omtf = create_encoder_omtf

  def predict(self, x):
    if self.omtf_input:
      encoder = self.create_encoder_omtf(x)
      loaded_model = self.loaded_model_omtf
    else:
      encoder = self.create_encoder(x)
      loaded_model = self.loaded_model

    x_new = encoder.get_x()
    y = loaded_model.predict(x_new)
    z = encoder.get_x_mask()
    t = encoder.get_x_road()

    assert len(y) == 2
    y[0] /= self.reg_pt_scale
    y = np.moveaxis(np.asarray(y),0,-1)  # shape (2, n, 1) -> shape (n, 1, 2)
    return (x_new, y, z, t)

  def run(self, x):
    x_new = np.array([], dtype=np.float32)
    y = np.array([], dtype=np.float32)
    z = np.array([], dtype=np.float32)
    t = np.array([], dtype=np.float32)
    if len(x) == 0:
      return (x_new, y, z, t)

    (x_new, y, z, t) = self.predict(x)
    return (x_new, y, z, t)


# Track producer module
class TrackProducer(object):
  def __init__(self, omtf_input=False, run2_input=False):
    self.omtf_input = omtf_input
    self.run2_input = run2_input

    self.discr_pt_cut = 8.
    self.discr_pt_cut_high = 14.

    self.s_min = 0.
    self.s_max = 60.
    self.s_nbins = 120
    self.s_step = (self.s_max - self.s_min)/self.s_nbins
    self.s_lut =[ 1.8103,  1.5499,  1.5994,  1.8430,  2.2030,  2.6341,  3.1130,  3.6314,
                  4.1913,  4.7920,  5.4080,  6.0314,  6.6592,  7.2948,  7.9427,  8.6010,
                  9.2604,  9.9203, 10.5863, 11.2478, 11.9017, 12.5720, 13.2714, 13.9870,
                 14.7056, 15.4150, 16.1127, 16.8182, 17.5527, 18.3129, 19.0717, 19.8404,
                 20.6285, 21.4527, 22.2358, 22.9200, 23.5376, 24.1071, 24.6601, 25.2473,
                 25.8888, 26.5613, 27.2474, 27.9908, 28.8538, 29.8815, 31.0003, 31.9688,
                 32.8233, 33.5948, 34.3189, 35.0252, 35.6697, 36.2580, 36.8490, 37.5502,
                 38.3722, 39.3099, 40.3040, 41.2324, 42.1571, 43.1702, 44.2244, 45.2144,
                 46.1481, 47.1148, 48.2273, 49.3547, 50.4047, 51.3084, 52.1028, 52.8775,
                 53.6479, 54.4168, 55.1850, 55.9528, 56.7204, 57.4879, 58.2552, 59.0225,
                 59.7898, 60.5570, 61.3242, 62.0914, 62.8585, 63.6257, 64.3928, 65.1600,
                 65.9271, 66.6942, 67.4613, 68.2284, 68.9955, 69.7626, 70.5297, 71.2968,
                 72.0639, 72.8310, 73.5981, 74.3652, 75.1323, 75.8994, 76.6665, 77.4336,
                 78.2006, 78.9677, 79.7348, 80.5019, 81.2690, 82.0361, 82.8032, 83.5703,
                 84.3374, 85.1044, 85.8715, 86.6386, 87.4057, 88.1728, 88.9399, 89.7070]
    #self.s_lut = np.linspace(self.s_min, self.s_max, num=self.s_nbins+1)[:-1]
    self.s_step = np.asarray(self.s_step)
    self.s_lut = np.asarray(self.s_lut)

  def get_trigger_pt(self, x, y_meas):
    xml_pt = np.abs(1.0/y_meas)
    if xml_pt <= 2.:  # do not use the LUT if below 2 GeV
      return xml_pt

    def digitize(x, bins=(self.s_nbins, self.s_min, self.s_max)):
      x = np.clip(x, bins[1], bins[2]-1e-5)
      binx = (x - bins[1]) / (bins[2] - bins[1]) * bins[0]
      return binx.astype(np.int32)

    def interpolate(x, x0, x1, y0, y1):
      y = (x - x0) / (x1 - x0) * (y1 - y0) + y0
      return y

    binx = digitize(xml_pt)
    if binx == self.s_nbins-1:  # check boundary
      binx -= 1

    x0, x1 = binx * self.s_step, (binx+1) * self.s_step
    y0, y1 = self.s_lut[binx], self.s_lut[binx+1]
    pt = interpolate(xml_pt, x0, x1, y0, y1)
    return pt

  def pass_trigger(self, ndof, modes, strg, zone, theta_median, y_meas, y_discr):
    ipt1 = strg
    ipt2 = find_pt_bin(y_meas)
    quality1 = find_emtf_road_quality(ipt1)
    quality2 = find_emtf_road_quality(ipt2)

    (mode, mode_me0, mode_omtf) = modes
    if self.omtf_input:
      mode_ok = (mode in (11,13,14,15)) or (mode_omtf == 3)
    else:
      mode_ok = (mode in (11,13,14,15)) or (mode_me0 == 3)

    strg_ok = quality2 <= (quality1+1)

    if mode_ok:
      if np.abs(1.0/y_meas) > self.discr_pt_cut_high:  # >14 GeV
        trigger = (y_discr > 0.7133) # 97.5% coverage
      elif np.abs(1.0/y_meas) > self.discr_pt_cut:  # 8-14 GeV
        trigger = (y_discr > 0.2882) # 97.5% coverage
      else:
        #trigger = (y_discr >= 0.)  # True
        trigger = (y_discr >= 0.) and strg_ok
    else:
      trigger = (y_discr < 0.)  # False
    return trigger

  def run(self, slim_roads, variables, predictions, x_mask_vars, x_road_vars):

    # __________________________________________________________________________
    # Extra pieces

    def get_ndof_from_x_mask(x_mask):
      assert(x_mask.shape[0] == nlayers)
      assert(x_mask.dtype == np.bool)
      valid = ~x_mask
      return valid.sum()

    def get_modes_from_x_mask(x_mask):
      assert(x_mask.shape[0] == nlayers)
      assert(x_mask.dtype == np.bool)
      valid = ~x_mask
      mode = np.int32(0)
      if np.any((valid[0], valid[1], valid[5], valid[9], valid[11])):   # ME1/1, ME1/2, RE1/2, GE1/1, ME0
        mode |= (1<<3)
      if np.any((valid[2], valid[6], valid[10])):  # ME2, RE2, GE2/1
        mode |= (1<<2)
      if np.any((valid[3], valid[7])):  # ME3, RE3
        mode |= (1<<1)
      if np.any((valid[4], valid[8])):  # ME4, RE4
        mode |= (1<<0)

      mode_me0 = np.int32(0)
      if valid[11]: # ME0
        mode_me0 |= (1 << 1)
      if valid[0]:  # ME1/1
        mode_me0 |= (1 << 0)

      mode_mb1 = np.int32(0)
      if valid[12]: # MB1
        mode_mb1 |= (1 << 1)
      if np.any((valid[1], valid[2], valid[3], valid[5], valid[6], valid[7], valid[13], valid[14])):
        mode_mb1 |= (1 << 0)

      mode_mb2 = np.int32(0)
      if valid[13]: # MB2
        mode_mb2 |= (1 << 1)
      if np.any((valid[1], valid[2], valid[3], valid[5], valid[6], valid[7], valid[14])):
        mode_mb2 |= (1 << 0)

      mode_me13 = np.int32(0)
      if np.any((valid[1], valid[5])):  # ME1/2+3, RE1/2+3
        mode_me13 |= (1 << 1)
      if np.any((valid[2], valid[3], valid[6], valid[7])):
        mode_me13 |= (1 << 0)

      #mode_me22 = np.int32(0)
      #if np.any((valid[2], valid[6])):  # ME2/2, RE2/2+3
      #  mode_me22 |= (1 << 1)
      #if np.any((valid[3], valid[7])):
      #  mode_me22 |= (1 << 0)

      mode_omtf = np.max((mode_mb1, mode_mb2, mode_me13))
      return (mode, mode_me0, mode_omtf)

    # __________________________________________________________________________
    assert(len(slim_roads) == len(variables))
    assert(len(slim_roads) == len(predictions))
    assert(len(slim_roads) == len(x_mask_vars))
    assert(len(slim_roads) == len(x_road_vars))

    tracks = []

    for myroad, x, y, x_mask, x_road in zip(slim_roads, variables, predictions, x_mask_vars, x_road_vars):
      assert(len(x.shape) == 1)
      assert(y.shape == (1,2))
      assert(x_mask.shape == (nlayers,))
      assert(x_road.shape == (3,))

      y_meas = np.asscalar(y[0,0])
      y_discr = np.asscalar(y[0,1])
      ndof = get_ndof_from_x_mask(x_mask)
      modes = get_modes_from_x_mask(x_mask)
      strg, zone, theta_median = x_road

      passed = self.pass_trigger(ndof, modes, strg, zone, theta_median, y_meas, y_discr)
      xml_pt = np.abs(1.0/y_meas)
      pt = self.get_trigger_pt(x, y_meas)

      if passed:
        trk_q = np.sign(y_meas)
        trk_emtf_phi = myroad.id[4]
        trk_emtf_theta = theta_median
        trk = Track(myroad.id, myroad.hits, modes[0], zone, xml_pt, pt, trk_q, trk_emtf_phi, trk_emtf_theta, ndof, y_discr)
        tracks.append(trk)
    return tracks


# Ghost busting module
class GhostBusting(object):
  def __init__(self):
    pass

  def run(self, tracks):
    tracks_after_gb = []

    # Sort by (zone, chi2)
    # zone is reordered such that zone 6 has the lowest priority.
    tracks.sort(key=lambda track: ((track.zone+1) % 7, track.chi2), reverse=True)

    # Iterate over tracks and remove duplicates (ghosts)
    for i, track in enumerate(tracks):
      keep = True

      # Do not share ME1/1, ME1/2, ME0, MB1, MB2
      for j, track_to_check in enumerate(tracks[:i]):
        hits_i = [(hit.emtf_layer, hit.emtf_phi) for hit in track.hits if hit.emtf_layer in (0,1,11,12,13)]
        hits_j = [(hit.emtf_layer, hit.emtf_phi) for hit in track_to_check.hits if hit.emtf_layer in (0,1,11,12,13)]
        if set(hits_i).intersection(hits_j):
          keep = False
          break

      if keep:
        tracks_after_gb.append(track)
    return tracks_after_gb


# ______________________________________________________________________________
# Analysis: dummy

class DummyAnalysis(object):
  def run(self, omtf_input=False, run2_input=False):
    # Load tree
    if omtf_input:
      tree = load_pgun_omtf()
    else:
      tree = load_pgun()

    # Loop over events
    for ievt, evt in enumerate(tree):
      if maxEvents != -1 and ievt == maxEvents:
        break

      print("Processing event: {0}".format(ievt))

      # Hits
      for ihit, hit in enumerate(evt.hits):
        print(".. hit  {0} {1} {2} {3} {4} {5} {6} {7} {8} {9} {10} {11}".format(ihit, hit.bx, hit.type, hit.station, hit.ring, hit.sector, hit.fr, hit.sim_phi, hit.sim_theta, hit.time, hit.sim_tp1, hit.sim_tp2))
      # Tracks
      for itrk, trk in enumerate(evt.tracks):
        print(".. trk  {0} {1} {2} {3} {4} {5} {6} {7}".format(itrk, trk.sector, trk.mode, trk.pt, trk.phi, trk.eta, trk.theta, trk.q))
      # Gen particles
      for ipart, part in enumerate(evt.particles):
        print(".. part {0} {1} {2} {3} {4} {5}".format(ipart, part.pt, part.phi, part.eta, part.theta, part.q))

    # End loop over events
    unload_tree()


# ______________________________________________________________________________
# Analysis: roads

class RoadsAnalysis(object):
  def run(self, omtf_input=False, run2_input=False):
    # Book histograms
    histograms = {}
    eff_pt_bins = (0., 0.5, 1., 1.5, 2., 3., 4., 5., 6., 7., 8., 10., 12., 14., 16., 18., 20., 22., 24., 27., 30., 34., 40., 48., 60., 80., 120.)

    for k in ("denom", "numer"):
      hname = "eff_vs_genpt_%s" % k
      histograms[hname] = Hist(eff_pt_bins, name=hname, title="; gen p_{T} [GeV]", type='F')
      histograms[hname].Sumw2()

      hname = "eff_vs_geneta_%s" % k
      histograms[hname] = Hist(70, 1.1, 2.5, name=hname, title="; gen |#eta|", type='F')
      histograms[hname].Sumw2()

      hname = "eff_vs_genphi_%s" % k
      histograms[hname] = Hist(64, -3.2, 3.2, name=hname, title="; gen #phi", type='F')
      histograms[hname].Sumw2()

    # Load tree
    if omtf_input:
      tree = load_pgun_batch_omtf(jobid)
    else:
      tree = load_pgun_batch(jobid)

    # Workers
    bank = PatternBank(bankfile)
    recog = PatternRecognition(bank, omtf_input=omtf_input, run2_input=run2_input)
    clean = RoadCleaning()
    slim = RoadSlimming(bank)
    out_particles = []
    out_roads = []
    npassed, ntotal = 0, 0

    # Event range
    n = -1

    # __________________________________________________________________________
    # Loop over events
    for ievt, evt in enumerate(tree):
      if n != -1 and ievt == n:
        break

      if len(evt.particles) == 0:
        continue

      part = evt.particles[0]  # particle gun
      part.invpt = np.true_divide(part.q, part.pt)

      roads = recog.run(evt.hits)
      clean_roads = clean.run(roads)
      slim_roads = slim.run(clean_roads)
      assert(len(clean_roads) == len(slim_roads))

      if len(slim_roads) > 0:
        mypart = Particle(part.pt, part.eta, part.phi, part.q, part.vx, part.vy, part.vz)
        out_particles.append(mypart)
        out_roads.append(slim_roads[0])

      if omtf_input:
        is_important = lambda part: (0.8 <= abs(part.eta) <= 1.24) and (part.bx == 0) and (part.pt > 5.)
        is_possible = lambda hits: (any([(hit.type == kDT and hit.station == 1) for hit in hits]) and any([(hit.type == kDT and 2 <= hit.station <= 3) for hit in hits])) or \
            (any([(hit.type == kCSC and hit.station == 1) for hit in hits]) and any([(hit.type == kDT and 1 <= hit.station <= 2) for hit in hits])) or \
            (any([(hit.type == kCSC and hit.station == 1) for hit in hits]) and any([(hit.type == kCSC and 2 <= hit.station <= 3) for hit in hits]))
      else:
        is_important = lambda part: (1.24 <= abs(part.eta) <= 2.4) and (part.bx == 0) and (part.pt > 4.)
        is_possible = lambda hits: any([((hit.type == kCSC or hit.type == kME0) and hit.station == 1) for hit in hits]) and \
            any([(hit.type == kCSC and hit.station >= 2) for hit in hits])

      if ievt < 20 or (len(clean_roads) == 0 and is_important(part) and is_possible(evt.hits)):
        print("evt {0} has {1} roads and {2} clean roads".format(ievt, len(roads), len(clean_roads)))
        print(".. part invpt: {0} pt: {1} eta: {2} phi: {3}".format(part.invpt, part.pt, part.eta, part.phi))
        part.ipt = find_pt_bin(part.invpt)
        part.ieta = find_eta_bin(part.eta)
        #part.exphi = emtf_extrapolation(part)
        #part.sector = find_sector(part.exphi)
        #part.endcap = find_endcap(part.eta)
        #part.emtf_phi = calc_phi_loc_int(np.rad2deg(part.exphi), part.sector)
        #part.emtf_theta = calc_theta_int(calc_theta_deg_from_eta(part.eta), part.endcap)
        #part_road_id = (part.endcap, part.sector, part.ipt, part.ieta, (part.emtf_phi+16)//32)
        #part_nhits = sum([1 for hit in evt.hits if hit.endcap == part.endcap and hit.sector == part.sector])
        #print(".. part road id: {0} nhits: {1} exphi: {2} emtf_phi: {3}".format(part_road_id, part_nhits, part.exphi, part.emtf_phi))
        for ihit, hit in enumerate(evt.hits):
          hit_id = (hit.type, hit.station, hit.ring, find_endsec(hit.endcap, hit.sector), hit.fr, hit.bx)
          hit_sim_tp = (hit.sim_tp1 == 0 and hit.sim_tp2 == 0)
          print(".. .. hit {0} id: {1} lay: {2} ph: {3} ({4}) th: {5} bd: {6} qual: {7} tp: {8}".format(ihit, hit_id, find_emtf_layer(hit), hit.emtf_phi, find_pattern_x(hit.emtf_phi), hit.emtf_theta, find_emtf_bend(hit), find_emtf_quality(hit), hit_sim_tp))
        for iroad, myroad in enumerate(sorted(roads, key=lambda x: x.id)):
          print(".. road {0} id: {1} nhits: {2} mode: {3} qual: {4} sort: {5}".format(iroad, myroad.id, len(myroad.hits), myroad.mode, myroad.quality, myroad.sort_code))
        for iroad, myroad in enumerate(clean_roads):
          print(".. croad {0} id: {1} nhits: {2} mode: {3} qual: {4} sort: {5}".format(iroad, myroad.id, len(myroad.hits), myroad.mode, myroad.quality, myroad.sort_code))
          for ihit, myhit in enumerate(myroad.hits):
            print(".. .. hit {0} id: {1} lay: {2} ph: {3} th: {4} tp: {5}".format(ihit, myhit.id, myhit.emtf_layer, myhit.emtf_phi, myhit.emtf_theta, myhit.sim_tp))
        for iroad, myroad in enumerate(slim_roads):
          print(".. sroad {0} id: {1} nhits: {2} mode: {3} qual: {4} sort: {5}".format(iroad, myroad.id, len(myroad.hits), myroad.mode, myroad.quality, myroad.sort_code))
          for ihit, myhit in enumerate(myroad.hits):
            print(".. .. hit {0} id: {1} lay: {2} ph: {3} th: {4} tp: {5}".format(ihit, myhit.id, myhit.emtf_layer, myhit.emtf_phi, myhit.emtf_theta, myhit.sim_tp))

      # Quick efficiency
      if is_important(part):
        trigger = len(clean_roads) > 0
        ntotal += 1
        if trigger:
          npassed += 1
        #else:
        #  print("evt {0} FAIL".format(ievt))

        hname = "eff_vs_genpt_denom"
        histograms[hname].fill(part.pt)
        if trigger:
          hname = "eff_vs_genpt_numer"
          histograms[hname].fill(part.pt)

        if part.pt > 20.:
          hname = "eff_vs_geneta_denom"
          histograms[hname].fill(abs(part.eta))
          if trigger:
            hname = "eff_vs_geneta_numer"
            histograms[hname].fill(abs(part.eta))
          hname = "eff_vs_genphi_denom"
          histograms[hname].fill(part.phi)
          if trigger:
            hname = "eff_vs_genphi_numer"
            histograms[hname].fill(part.phi)

    # End loop over events
    unload_tree()

    print('[INFO] npassed/ntotal: %i/%i = %f' % (npassed, ntotal, float(npassed)/ntotal))

    # __________________________________________________________________________
    # Save objects
    outfile = 'histos_tba.npz'
    if use_condor:
      outfile = 'histos_tba_%i.npz' % jobid
    print('[INFO] Creating file: %s' % outfile)
    if True:
      assert(len(out_particles) == len(out_roads))
      parameters = particles_to_parameters(out_particles)
      variables = roads_to_variables(out_roads)
      np.savez_compressed(outfile, parameters=parameters, variables=variables)


# ______________________________________________________________________________
# Analysis: rates

class RatesAnalysis(object):
  def run(self, omtf_input=False, run2_input=False, pileup=200):
    # Book histograms
    histograms = {}
    hname = "nevents"
    histograms[hname] = Hist(5, 0, 5, name=hname, title="; count", type='F')
    for m in ("emtf", "emtf2026"):
      hname = "highest_%s_absEtaMin0.8_absEtaMax2.4_qmin12_pt" % m
      histograms[hname] = Hist(100, 0., 100., name=hname, title="; p_{T} [GeV]; entries", type='F')
      hname = "highest_%s_absEtaMin1.24_absEtaMax2.4_qmin12_pt" % m
      histograms[hname] = Hist(100, 0., 100., name=hname, title="; p_{T} [GeV]; entries", type='F')
      hname = "highest_%s_absEtaMin0.8_absEtaMax1.24_qmin12_pt" % m
      histograms[hname] = Hist(100, 0., 100., name=hname, title="; p_{T} [GeV]; entries", type='F')
      hname = "highest_%s_absEtaMin1.24_absEtaMax1.65_qmin12_pt" % m
      histograms[hname] = Hist(100, 0., 100., name=hname, title="; p_{T} [GeV]; entries", type='F')
      hname = "highest_%s_absEtaMin1.65_absEtaMax2.15_qmin12_pt" % m
      histograms[hname] = Hist(100, 0., 100., name=hname, title="; p_{T} [GeV]; entries", type='F')
      hname = "highest_%s_absEtaMin2.15_absEtaMax2.4_qmin12_pt" % m
      histograms[hname] = Hist(100, 0., 100., name=hname, title="; p_{T} [GeV]; entries", type='F')

      for l in xrange(14,22+1):
        hname = "%s_ptmin%i_qmin12_eta" % (m,l)
        histograms[hname] = Hist(18, 0.75, 2.55, name=hname, title="; |#eta|; entries", type='F')

    # Load tree
    tree = load_minbias_batch(jobid, pileup=pileup)

    # Workers
    bank = PatternBank(bankfile)
    recog1, recog2 = PatternRecognition(bank, omtf_input=False, run2_input=run2_input), PatternRecognition(bank, omtf_input=True, run2_input=run2_input)
    clean = RoadCleaning()
    slim = RoadSlimming(bank)
    ptassig1, ptassig2 = PtAssignment(kerasfile, omtf_input=False, run2_input=run2_input), PtAssignment(kerasfile, omtf_input=True, run2_input=run2_input)
    trkprod1, trkprod2 = TrackProducer(omtf_input=False, run2_input=run2_input), TrackProducer(omtf_input=True, run2_input=run2_input)
    ghost = GhostBusting()

    # Event range
    n = -1

    # __________________________________________________________________________
    # Loop over events
    for ievt, evt in enumerate(tree):
      if n != -1 and ievt == n:
        break

      # EMTF mode
      roads = recog1.run(evt.hits)
      clean_roads = clean.run(roads)
      slim_roads = slim.run(clean_roads)
      variables = roads_to_variables(slim_roads)
      variables, predictions, x_mask_vars, x_road_vars = ptassig1.run(variables)
      tracks = trkprod1.run(slim_roads, variables, predictions, x_mask_vars, x_road_vars)

      # OMTF mode
      roads2 = recog2.run(evt.hits)
      clean_roads2 = clean.run(roads2)
      slim_roads2 = slim.run(clean_roads2)
      variables2 = roads_to_variables(slim_roads2)
      variables2, predictions2, x_mask_vars2, x_road_vars2 = ptassig2.run(variables2)
      tracks2 = trkprod2.run(slim_roads2, variables2, predictions2, x_mask_vars2, x_road_vars2)

      # Ghost busting
      emtf2026_tracks = ghost.run(tracks + tracks2)

      found_high_pt_tracks = any(map(lambda trk: trk.pt > 20., emtf2026_tracks))

      if found_high_pt_tracks:
        print("evt {0} has {1} roads, {2} clean roads, {3} old tracks, {4} new tracks".format(ievt, len(roads), len(clean_roads), len(evt.tracks), len(emtf2026_tracks)))
        for ipart, part in enumerate(evt.particles):
          if part.pt > 5.:
            part.invpt = np.true_divide(part.q, part.pt)
            print(".. part invpt: {0} pt: {1} eta: {2} phi: {3}".format(part.invpt, part.pt, part.eta, part.phi))
        for iroad, myroad in enumerate(clean_roads):
          print(".. croad {0} id: {1} nhits: {2} mode: {3} qual: {4} sort: {5}".format(iroad, myroad.id, len(myroad.hits), myroad.mode, myroad.quality, myroad.sort_code))
          #for ihit, myhit in enumerate(myroad.hits):
          #  print(".. .. hit {0} id: {1} lay: {2} ph: {3} th: {4} tp: {5}".format(ihit, myhit.id, myhit.emtf_layer, myhit.emtf_phi, myhit.emtf_theta, myhit.sim_tp))
        for itrk, mytrk in enumerate(emtf2026_tracks):
          print(".. trk {0} id: {1} nhits: {2} mode: {3} pt: {4} ndof: {5} chi2: {6}".format(itrk, mytrk.id, len(mytrk.hits), mytrk.mode, mytrk.pt, mytrk.ndof, mytrk.chi2))
          for ihit, myhit in enumerate(mytrk.hits):
            print(".. .. hit {0} id: {1} lay: {2} ph: {3} th: {4} tp: {5}".format(ihit, myhit.id, myhit.emtf_layer, myhit.emtf_phi, myhit.emtf_theta, myhit.sim_tp))
        for itrk, mytrk in enumerate(evt.tracks):
          print(".. otrk {0} id: {1} mode: {2} pt: {3}".format(itrk, (mytrk.endcap, mytrk.sector), mytrk.mode, mytrk.pt))

      # ________________________________________________________________________
      # Fill histograms
      histograms["nevents"].fill(1.0)

      def fill_highest_pt():
        highest_pt = -999999.
        for itrk, trk in enumerate(tracks):
          if select(trk):
            if highest_pt < trk.pt:  # using scaled pT
              highest_pt = trk.pt
        if highest_pt > 0.:
          highest_pt = min(100.-1e-3, highest_pt)
          histograms[hname].fill(highest_pt)

      def fill_eta():
        h = histograms[hname]
        eta_bins = [False] * (h.GetNbinsX()+2)
        for itrk, trk in enumerate(tracks):
          if select(trk):  # using scaled pT
            b = h.FindFixBin(abs(trk.eta))
            eta_bins[b] = True
        for b in xrange(len(eta_bins)):
          if eta_bins[b]:
            h.fill(h.GetBinCenter(b))

      tracks = evt.tracks
      select = lambda trk: trk and (0.8 <= abs(trk.eta) <= 2.4) and (trk.bx == 0) and (trk.mode in (11,13,14,15))
      hname = "highest_emtf_absEtaMin0.8_absEtaMax2.4_qmin12_pt"
      fill_highest_pt()
      select = lambda trk: trk and (1.24 <= abs(trk.eta) <= 2.4) and (trk.bx == 0) and (trk.mode in (11,13,14,15))
      hname = "highest_emtf_absEtaMin1.24_absEtaMax2.4_qmin12_pt"
      fill_highest_pt()
      select = lambda trk: trk and (0.8 <= abs(trk.eta) < 1.24) and (trk.bx == 0) and (trk.mode in (11,13,14,15))
      hname = "highest_emtf_absEtaMin0.8_absEtaMax1.24_qmin12_pt"
      fill_highest_pt()
      select = lambda trk: trk and (1.24 <= abs(trk.eta) < 1.65) and (trk.bx == 0) and (trk.mode in (11,13,14,15))
      hname = "highest_emtf_absEtaMin1.24_absEtaMax1.65_qmin12_pt"
      fill_highest_pt()
      select = lambda trk: trk and (1.65 <= abs(trk.eta) < 2.15) and (trk.bx == 0) and (trk.mode in (11,13,14,15))
      hname = "highest_emtf_absEtaMin1.65_absEtaMax2.15_qmin12_pt"
      fill_highest_pt()
      select = lambda trk: trk and (2.15 <= abs(trk.eta) <= 2.4) and (trk.bx == 0) and (trk.mode in (11,13,14,15))
      hname = "highest_emtf_absEtaMin2.15_absEtaMax2.4_qmin12_pt"
      fill_highest_pt()
      for l in xrange(14,22+1):
        select = lambda trk: trk and (0 <= abs(trk.eta) <= 9.9) and (trk.bx == 0) and (trk.mode in (11,13,14,15)) and (trk.pt > float(l))
        hname = "emtf_ptmin%i_qmin12_eta" % (l)
        fill_eta()

      tracks = emtf2026_tracks
      select = lambda trk: trk and (0.8 <= abs(trk.eta) <= 2.4) and trk.zone in (0,1,2,3,4,5,6)
      hname = "highest_emtf2026_absEtaMin0.8_absEtaMax2.4_qmin12_pt"
      fill_highest_pt()
      select = lambda trk: trk and (1.24 <= abs(trk.eta) <= 2.4) and trk.zone in (0,1,2,3,4,5)
      hname = "highest_emtf2026_absEtaMin1.24_absEtaMax2.4_qmin12_pt"
      fill_highest_pt()
      select = lambda trk: trk and (0.8 <= abs(trk.eta) <= 1.24) and trk.zone in (6,)
      hname = "highest_emtf2026_absEtaMin0.8_absEtaMax1.24_qmin12_pt"
      fill_highest_pt()
      select = lambda trk: trk and (1.24 <= abs(trk.eta) <= 1.65) and trk.zone in (0,1,2,3,4,5)
      hname = "highest_emtf2026_absEtaMin1.24_absEtaMax1.65_qmin12_pt"
      fill_highest_pt()
      select = lambda trk: trk and (1.65 <= abs(trk.eta) <= 2.15) and trk.zone in (0,1,2,3,4,5)
      hname = "highest_emtf2026_absEtaMin1.65_absEtaMax2.15_qmin12_pt"
      fill_highest_pt()
      select = lambda trk: trk and (2.15 <= abs(trk.eta) <= 2.4) and trk.zone in (0,1,2,3,4,5)
      hname = "highest_emtf2026_absEtaMin2.15_absEtaMax2.4_qmin12_pt"
      fill_highest_pt()
      for l in xrange(14,22+1):
        select = lambda trk: trk and (0 <= abs(trk.eta) <= 9.9) and (trk.pt > float(l))
        hname = "emtf2026_ptmin%i_qmin12_eta" % (l)
        fill_eta()

    # End loop over events
    unload_tree()

    # __________________________________________________________________________
    # Save histograms
    outfile = 'histos_tbb.root'
    if use_condor:
      outfile = 'histos_tbb_%i.root' % jobid
    print('[INFO] Creating file: %s' % outfile)
    with root_open(outfile, 'recreate') as f:
      hnames = []
      hname = "nevents"
      hnames.append("nevents")
      for m in ("emtf", "emtf2026"):
        hname = "highest_%s_absEtaMin0.8_absEtaMax2.4_qmin12_pt" % m
        hnames.append(hname)
        hname = "highest_%s_absEtaMin1.24_absEtaMax2.4_qmin12_pt" % m
        hnames.append(hname)
        hname = "highest_%s_absEtaMin0.8_absEtaMax1.24_qmin12_pt" % m
        hnames.append(hname)
        hname = "highest_%s_absEtaMin1.24_absEtaMax1.65_qmin12_pt" % m
        hnames.append(hname)
        hname = "highest_%s_absEtaMin1.65_absEtaMax2.15_qmin12_pt" % m
        hnames.append(hname)
        hname = "highest_%s_absEtaMin2.15_absEtaMax2.4_qmin12_pt" % m
        hnames.append(hname)
        for l in xrange(14,22+1):
          hname = "%s_ptmin%i_qmin12_eta" % (m,l)
          hnames.append(hname)
      for hname in hnames:
        h = histograms[hname]
        h.Write()


# ______________________________________________________________________________
# Analysis: effie

class EffieAnalysis(object):
  def run(self, omtf_input=False, run2_input=False):
    # Book histograms
    histograms = {}
    eff_pt_bins = (0., 0.5, 1., 1.5, 2., 3., 4., 5., 6., 7., 8., 10., 12., 14., 16., 18., 20., 22., 24., 27., 30., 34., 40., 48., 60., 80., 120.)

    for m in ("emtf", "emtf2026"):
      for l in (0, 10, 15, 20, 30, 40, 50):
        for k in ("denom", "numer"):
          hname = "%s_eff_vs_genpt_l1pt%i_%s" % (m,l,k)
          histograms[hname] = Hist(eff_pt_bins, name=hname, title="; gen p_{T} [GeV]", type='F')
          hname = "%s_eff_vs_genphi_l1pt%i_%s" % (m,l,k)
          histograms[hname] = Hist(76, -190, 190, name=hname, title="; gen #phi {gen p_{T} > 20 GeV}", type='F')
          hname = "%s_eff_vs_geneta_l1pt%i_%s" % (m,l,k)
          histograms[hname] = Hist(85, 0.8, 2.5, name=hname, title="; gen |#eta| {gen p_{T} > 20 GeV}", type='F')
          hname = "%s_eff_vs_geneta_genpt30_l1pt%i_%s" % (m,l,k)
          histograms[hname] = Hist(85, 0.8, 2.5, name=hname, title="; gen |#eta| {gen p_{T} > 30 GeV}", type='F')

      hname = "%s_l1pt_vs_genpt" % m
      histograms[hname] = Hist2D(100, -0.5, 0.5, 300, -0.5, 0.5, name=hname, title="; gen q/p_{T} [1/GeV]; q/p_{T} [1/GeV]", type='F')
      hname = "%s_l1ptres_vs_genpt" % m
      histograms[hname] = Hist2D(100, -0.5, 0.5, 300, -1, 2, name=hname, title="; gen q/p_{T} [1/GeV]; #Delta(p_{T})/p_{T}", type='F')

    # Load tree
    if omtf_input:
      tree = load_pgun_batch_omtf(jobid)
    else:
      tree = load_pgun_batch(jobid)

    # Workers
    bank = PatternBank(bankfile)
    recog1, recog2 = PatternRecognition(bank, omtf_input=False, run2_input=run2_input), PatternRecognition(bank, omtf_input=True, run2_input=run2_input)
    clean = RoadCleaning()
    slim = RoadSlimming(bank)
    ptassig1, ptassig2 = PtAssignment(kerasfile, omtf_input=False, run2_input=run2_input), PtAssignment(kerasfile, omtf_input=True, run2_input=run2_input)
    trkprod1, trkprod2 = TrackProducer(omtf_input=False, run2_input=run2_input), TrackProducer(omtf_input=True, run2_input=run2_input)
    ghost = GhostBusting()

    # Event range
    n = -1

    # __________________________________________________________________________
    # Loop over events
    for ievt, evt in enumerate(tree):
      if n != -1 and ievt == n:
        break

      if len(evt.particles) == 0:
        continue

      part = evt.particles[0]  # particle gun
      part.invpt = np.true_divide(part.q, part.pt)

      # EMTF mode
      roads = recog1.run(evt.hits)
      clean_roads = clean.run(roads)
      slim_roads = slim.run(clean_roads)
      variables = roads_to_variables(slim_roads)
      variables, predictions, x_mask_vars, x_road_vars = ptassig1.run(variables)
      tracks = trkprod1.run(slim_roads, variables, predictions, x_mask_vars, x_road_vars)

      # OMTF mode
      roads2 = recog2.run(evt.hits)
      clean_roads2 = clean.run(roads2)
      slim_roads2 = slim.run(clean_roads2)
      variables2 = roads_to_variables(slim_roads2)
      variables2, predictions2, x_mask_vars2, x_road_vars2 = ptassig2.run(variables2)
      tracks2 = trkprod2.run(slim_roads2, variables2, predictions2, x_mask_vars2, x_road_vars2)

      # Ghost busting
      emtf2026_tracks = ghost.run(tracks + tracks2)

      if ievt < 20 and False:
        print("evt {0} has {1} roads, {2} clean roads, {3} old tracks, {4} new tracks".format(ievt, len(roads), len(clean_roads), len(evt.tracks), len(emtf2026_tracks)))
        for itrk, mytrk in enumerate(emtf2026_tracks):
          y = np.true_divide(part.q, part.pt)
          y_pred = np.true_divide(mytrk.q, mytrk.xml_pt)
          print(".. {0} {1}".format(y, y_pred))

      # ________________________________________________________________________
      # Fill histograms
      def fill_efficiency_pt():
        if select_part(part):
          trigger = any([select_track(trk) for trk in tracks])  # using scaled pT
          denom = histograms[hname + "_denom"]
          numer = histograms[hname + "_numer"]
          denom.fill(part.pt)
          if trigger:
            numer.fill(part.pt)

      def fill_efficiency_phi():
        if select_part(part):
          trigger = any([select_track(trk) for trk in tracks])  # using scaled pT
          denom = histograms[hname + "_denom"]
          numer = histograms[hname + "_numer"]
          denom.fill(np.rad2deg(part.phi))
          if trigger:
            numer.fill(np.rad2deg(part.phi))

      def fill_efficiency_eta():
        if (part.bx == 0):
          trigger = any([select_track(trk) for trk in tracks])  # using scaled pT
          denom = histograms[hname + "_denom"]
          numer = histograms[hname + "_numer"]
          denom.fill(abs(part.eta))
          if trigger:
            numer.fill(abs(part.eta))

      def fill_resolution():
        if (part.bx == 0):
          trigger = any([select_track(trk) for trk in tracks])  # using scaled pT
          if trigger:
            trk = tracks[0]
            trk.invpt = np.true_divide(trk.q, trk.xml_pt)  # using unscaled pT
            histograms[hname1].fill(part.invpt, trk.invpt)
            histograms[hname2].fill(abs(part.invpt), (abs(1.0/trk.invpt) - abs(1.0/part.invpt))/abs(1.0/part.invpt))

      for l in (0, 10, 15, 20, 30, 40, 50):
        tracks = evt.tracks
        select_part = lambda part: (1.24 <= abs(part.eta) <= 2.4) and (part.bx == 0)
        select_track = lambda trk: trk and (1.24 <= abs(trk.eta) <= 2.4) and (trk.bx == 0) and (trk.mode in (11,13,14,15)) and (trk.pt > float(l))
        #
        hname = "emtf_eff_vs_genpt_l1pt%i" % (l)
        fill_efficiency_pt()
        if part.pt > 20.:
          hname = "emtf_eff_vs_genphi_l1pt%i" % (l)
          fill_efficiency_phi()
          hname = "emtf_eff_vs_geneta_l1pt%i" % (l)
          fill_efficiency_eta()
        if part.pt > 30.:
          hname = "emtf_eff_vs_geneta_genpt30_l1pt%i" % (l)
          fill_efficiency_eta()
        if l == 0:
          hname1 = "emtf_l1pt_vs_genpt"
          hname2 = "emtf_l1ptres_vs_genpt"
          fill_resolution()

        tracks = emtf2026_tracks
        if omtf_input:
          select_part = lambda part: (0.8 <= abs(part.eta) <= 1.24) and (part.bx == 0)
          #select_track = lambda trk: trk and (0.8 <= abs(trk.eta) <= 1.24) and trk.zone in (6,) and (trk.pt > float(l))
          select_track = lambda trk: trk and (0.75 <= abs(trk.eta) <= 1.4) and trk.zone in (6,) and (trk.pt > float(l))
        else:
          select_part = lambda part: (1.24 <= abs(part.eta) <= 2.4) and (part.bx == 0)
          #select_track = lambda trk: trk and (1.24 <= abs(trk.eta) <= 2.4) and trk.zone in (0,1,2,3,4,5) and (trk.pt > float(l))
          select_track = lambda trk: trk and (1.1 <= abs(trk.eta) <= 2.4) and trk.zone in (0,1,2,3,4,5) and (trk.pt > float(l))
        #
        hname = "emtf2026_eff_vs_genpt_l1pt%i" % (l)
        fill_efficiency_pt()
        if part.pt > 20.:
          hname = "emtf2026_eff_vs_genphi_l1pt%i" % (l)
          fill_efficiency_phi()
          hname = "emtf2026_eff_vs_geneta_l1pt%i" % (l)
          fill_efficiency_eta()
        if part.pt > 30.:
          hname = "emtf2026_eff_vs_geneta_genpt30_l1pt%i" % (l)
          fill_efficiency_eta()
        if l == 0:
          hname1 = "emtf2026_l1pt_vs_genpt"
          hname2 = "emtf2026_l1ptres_vs_genpt"
          fill_resolution()

    # End loop over events
    unload_tree()

    # __________________________________________________________________________
    # Save histograms
    outfile = 'histos_tbc.root'
    if use_condor:
      outfile = 'histos_tbc_%i.root' % jobid
    print('[INFO] Creating file: %s' % outfile)
    with root_open(outfile, 'recreate') as f:
      hnames = []
      for m in ("emtf", "emtf2026"):
        for l in (0, 10, 15, 20, 30, 40, 50):
          for k in ("denom", "numer"):
            hname = "%s_eff_vs_genpt_l1pt%i_%s" % (m,l,k)
            hnames.append(hname)
            hname = "%s_eff_vs_genphi_l1pt%i_%s" % (m,l,k)
            hnames.append(hname)
            hname = "%s_eff_vs_geneta_l1pt%i_%s" % (m,l,k)
            hnames.append(hname)
            hname = "%s_eff_vs_geneta_genpt30_l1pt%i_%s" % (m,l,k)
            hnames.append(hname)
        hname = "%s_l1pt_vs_genpt" % m
        hnames.append(hname)
        hname = "%s_l1ptres_vs_genpt" % m
        hnames.append(hname)
      for hname in hnames:
        h = histograms[hname]
        h.Write()


# ______________________________________________________________________________
# Analysis: mixing

class MixingAnalysis(object):
  def run(self, omtf_input=False, run2_input=False):
    tree = load_minbias_batch_for_mixing(jobid)

    # Workers
    bank = PatternBank(bankfile)
    recog = PatternRecognition(bank, omtf_input=omtf_input, run2_input=run2_input)
    clean = RoadCleaning()
    slim = RoadSlimming(bank)
    out_particles = []
    out_roads = []
    npassed, ntotal = 0, 0

    # Event range
    n = -1

    # __________________________________________________________________________
    # Loop over events
    for ievt, evt in enumerate(tree):
      if n != -1 and ievt == n:
        break

      roads = recog.run(evt.hits)
      clean_roads = clean.run(roads)
      slim_roads = slim.run(clean_roads)
      assert(len(clean_roads) == len(slim_roads))

      def find_highest_part_pt():
        highest_pt = -999999.
        for ipart, part in enumerate(evt.particles):
          if select_part(part):
            if highest_pt < part.pt:
              highest_pt = part.pt
        if highest_pt > 0.:
          highest_pt = min(100.-1e-3, highest_pt)
          return highest_pt

      def find_highest_track_pt():
        highest_pt = -999999.
        for itrk, trk in enumerate(evt.tracks):
          if select_track(trk):
            if highest_pt < trk.pt:  # using scaled pT
              highest_pt = trk.pt
        if highest_pt > 0.:
          highest_pt = min(100.-1e-3, highest_pt)
          return highest_pt

      if omtf_input:
        select_part = lambda part: (0.8 <= abs(part.eta) <= 1.24) and (part.bx == 0)
        select_track = lambda trk: trk and (0.8 <= abs(trk.eta) <= 1.24) and (trk.bx == 0) and (trk.mode > 0)
      else:
        select_part = lambda part: (1.24 <= abs(part.eta) <= 2.4) and (part.bx == 0)
        select_track = lambda trk: trk and (1.24 <= abs(trk.eta) <= 2.4) and (trk.bx == 0) and (trk.mode in (11,13,14,15))

      highest_part_pt = find_highest_part_pt()
      highest_track_pt = find_highest_track_pt()

      if len(slim_roads) > 0:
        part = (jobid, ievt, highest_part_pt, highest_track_pt)
        out_particles += [part for _ in xrange(len(slim_roads))]
        out_roads += slim_roads

      debug_event_list = set([2826, 2937, 3675, 4581, 4838, 5379, 7640])

      if ievt < 20 or ievt in debug_event_list:
        print("evt {0} has {1} roads, {2} clean roads, {3} old tracks, {4} new tracks".format(ievt, len(roads), len(clean_roads), len(evt.tracks), '?'))
        #for iroad, myroad in enumerate(sorted(roads, key=lambda x: x.id)):
        #  print(".. road {0} id: {1} nhits: {2} mode: {3} qual: {4} sort: {5}".format(iroad, myroad.id, len(myroad.hits), myroad.mode, myroad.quality, myroad.sort_code))
        for iroad, myroad in enumerate(clean_roads):
          print(".. croad {0} id: {1} nhits: {2} mode: {3} qual: {4} sort: {5}".format(iroad, myroad.id, len(myroad.hits), myroad.mode, myroad.quality, myroad.sort_code))
          for ihit, myhit in enumerate(myroad.hits):
            print(".. .. hit {0} id: {1} lay: {2} ph: {3} th: {4} tp: {5}".format(ihit, myhit.id, myhit.emtf_layer, myhit.emtf_phi, myhit.emtf_theta, myhit.sim_tp))

    # End loop over events
    unload_tree()

    # __________________________________________________________________________
    # Save objects
    outfile = 'histos_tbd.npz'
    if use_condor:
      outfile = 'histos_tbd_%i.npz' % jobid
    print('[INFO] Creating file: %s' % outfile)
    if True:
      assert(len(out_roads) == len(out_particles))
      variables = roads_to_variables(out_roads)
      aux = np.array(out_particles, dtype=np.float32)
      np.savez_compressed(outfile, variables=variables, aux=aux)


# ______________________________________________________________________________
# Settings

# Get number of events
#maxEvents = -1
#maxEvents = 4000000
maxEvents = 1000

# Condor or not
use_condor = ('CONDOR_EXEC' in os.environ)

# Algorithm (pick one)
#algo = 'default'  # phase 2
#algo = 'run3'
algo = 'omtf'
if use_condor:
  algo = sys.argv[1]

# Analysis mode (pick one)
#analysis = 'dummy'
#analysis = 'roads'
analysis = 'rates'
#analysis = 'effie'
#analysis = 'mixing'
#analysis = 'images'
if use_condor:
  analysis = sys.argv[2]

# Job id
jobid = 0
if use_condor:
  jobid = int(sys.argv[3])


# Input files
bankfile = 'pattern_bank_omtf.24.npz'

kerasfile = ['model.24.json', 'model_weights.24.h5', 'model_omtf.24.json', 'model_omtf_weights.24.h5']

infile_r = None  # input file handle

def purge_bad_files(infiles):
  good_files = []
  for infile in infiles:
    try:
      _ = TreeChain('ntupler/tree', infile)
      good_files.append(infile)
    except:
      pass
  return good_files

def load_pgun():
  global infile_r
  infile = 'ntuple_SingleMuon_Endcap_2GeV_add.4.root'
  infile_r = root_open(infile)
  tree = infile_r.ntupler.tree
  print('[INFO] Opening file: %s' % infile)

  # Define collection
  tree.define_collection(name='hits', prefix='vh_', size='vh_size')
  tree.define_collection(name='tracks', prefix='vt_', size='vt_size')
  tree.define_collection(name='particles', prefix='vp_', size='vp_size')
  #tree.define_collection(name='evt_info', prefix='ve_', size='ve_size')
  return tree

def load_pgun_batch(j):
  #global infile_r
  #infile_r = root_open('pippo.root', 'w')

  jj = np.split(np.arange(2000), 200)[j]
  infiles = []
  for j in jj:
    infiles.append('root://cmsxrootd-site.fnal.gov//store/group/l1upgrades/L1MuonTrigger/P2_10_4_0/SingleMuon_Endcap_2GeV/ParticleGuns/CRAB3/190207_042919/%04i/ntuple_SingleMuon_Endcap_%i.root' % ((j+1)/1000, (j+1)))
    infiles.append('root://cmsxrootd-site.fnal.gov//store/group/l1upgrades/L1MuonTrigger/P2_10_4_0/SingleMuon_Endcap2_2GeV/ParticleGuns/CRAB3/190207_043023/%04i/ntuple_SingleMuon_Endcap2_%i.root' % ((j+1)/1000, (j+1)))

  tree = TreeChain('ntupler/tree', infiles)
  print('[INFO] Opening file: %s' % ' '.join(infiles))

  # Define collection
  tree.define_collection(name='hits', prefix='vh_', size='vh_size')
  tree.define_collection(name='tracks', prefix='vt_', size='vt_size')
  tree.define_collection(name='particles', prefix='vp_', size='vp_size')
  #tree.define_collection(name='evt_info', prefix='ve_', size='ve_size')
  return tree

def load_pgun_omtf():
  global infile_r
  infile = 'ntuple_SingleMuon_Overlap_3GeV_add.4.root'
  infile_r = root_open(infile)
  tree = infile_r.ntupler.tree
  print('[INFO] Opening file: %s' % infile)

  # Define collection
  tree.define_collection(name='hits', prefix='vh_', size='vh_size')
  tree.define_collection(name='tracks', prefix='vt_', size='vt_size')
  tree.define_collection(name='particles', prefix='vp_', size='vp_size')
  #tree.define_collection(name='evt_info', prefix='ve_', size='ve_size')
  return tree

def load_pgun_batch_omtf(j):
  #global infile_r
  #infile_r = root_open('pippo.root', 'w')

  jj = np.split(np.arange(1000), 100)[j]
  infiles = []
  for j in jj:
    infiles.append('root://cmsxrootd-site.fnal.gov//store/group/l1upgrades/L1MuonTrigger/P2_10_4_0/SingleMuon_Overlap_3GeV/ParticleGuns/CRAB3/190206_065727/%04i/ntuple_SingleMuon_Overlap_%i.root' % ((j+1)/1000, (j+1)))
    infiles.append('root://cmsxrootd-site.fnal.gov//store/group/l1upgrades/L1MuonTrigger/P2_10_4_0/SingleMuon_Overlap2_3GeV/ParticleGuns/CRAB3/190206_065829/%04i/ntuple_SingleMuon_Overlap2_%i.root' % ((j+1)/1000, (j+1)))

  #infiles = purge_bad_files(infiles)
  tree = TreeChain('ntupler/tree', infiles)
  print('[INFO] Opening file: %s' % ' '.join(infiles))

  # Define collection
  tree.define_collection(name='hits', prefix='vh_', size='vh_size')
  tree.define_collection(name='tracks', prefix='vt_', size='vt_size')
  tree.define_collection(name='particles', prefix='vp_', size='vp_size')
  #tree.define_collection(name='evt_info', prefix='ve_', size='ve_size')
  return tree

def load_minbias_batch(j, pileup=200):
  global infile_r
  if pileup == 140:
    pufiles = ['root://cmsxrootd-site.fnal.gov//store/group/l1upgrades/L1MuonTrigger/P2_10_4_0/ntuple_SingleNeutrino_PU140/SingleNeutrino/CRAB3/190209_121318/0000/ntuple_SingleNeutrino_PU140_%i.root' % (i+1) for i in xrange(56)]
  elif pileup == 200:
    pufiles = ['root://cmsxrootd-site.fnal.gov//store/group/l1upgrades/L1MuonTrigger/P2_10_4_0/ntuple_SingleNeutrino_PU200/SingleNeutrino/CRAB3/190209_121428/0000/ntuple_SingleNeutrino_PU200_%i.root' % (i+1) for i in xrange(63)]
  elif pileup == 250:
    pufiles = ['root://cmsxrootd-site.fnal.gov//store/group/l1upgrades/L1MuonTrigger/P2_10_4_0/ntuple_SingleNeutrino_PU250/SingleNeutrino/CRAB3/190209_121524/0000/ntuple_SingleNeutrino_PU250_%i.root' % (i+1) for i in xrange(50)]
  elif pileup == 300:
    pufiles = ['root://cmsxrootd-site.fnal.gov//store/group/l1upgrades/L1MuonTrigger/P2_10_4_0/ntuple_SingleNeutrino_PU300/SingleNeutrino/CRAB3/190209_121619/0000/ntuple_SingleNeutrino_PU300_%i.root' % (i+1) for i in xrange(53)]
  else:
    raise RunTimeError('Cannot recognize pileup: {0}'.format(pileup))

  infile = pufiles[j]
  infile_r = root_open(infile)
  tree = infile_r.ntupler.tree
  print('[INFO] Opening file: %s' % infile)

  # Define collection
  tree.define_collection(name='hits', prefix='vh_', size='vh_size')
  tree.define_collection(name='tracks', prefix='vt_', size='vt_size')
  tree.define_collection(name='particles', prefix='vp_', size='vp_size')
  tree.define_collection(name='evt_info', prefix='ve_', size='ve_size')
  return tree

def load_minbias_batch_for_mixing(j):
  global infile_r
  pufiles = []
  # For training purposes
  pufiles += ['root://cmsxrootd-site.fnal.gov//store/group/l1upgrades/L1MuonTrigger/P2_10_4_0/ntuple_SingleNeutrino_PU140/SingleNeutrino/CRAB3/190209_121318/0000/ntuple_SingleNeutrino_PU140_%i.root' % (i+1) for i in xrange(20)]  # up to 20/56
  pufiles += ['root://cmsxrootd-site.fnal.gov//store/group/l1upgrades/L1MuonTrigger/P2_10_4_0/ntuple_SingleNeutrino_PU200/SingleNeutrino/CRAB3/190209_121428/0000/ntuple_SingleNeutrino_PU200_%i.root' % (i+1) for i in xrange(30)]  # up to 30/63
  #pufiles += ['root://cmsxrootd-site.fnal.gov//store/group/l1upgrades/L1MuonTrigger/P2_10_4_0/ntuple_SingleNeutrino_PU250/SingleNeutrino/CRAB3/190209_121524/0000/ntuple_SingleNeutrino_PU250_%i.root' % (i+1) for i in xrange(20)]  # up to 20/50
  #pufiles += ['root://cmsxrootd-site.fnal.gov//store/group/l1upgrades/L1MuonTrigger/P2_10_4_0/ntuple_SingleNeutrino_PU300/SingleNeutrino/CRAB3/190209_121619/0000/ntuple_SingleNeutrino_PU300_%i.root' % (i+1) for i in xrange(20)]  # up to 20/53
  pufiles += ['root://cmsxrootd-site.fnal.gov//store/group/l1upgrades/L1MuonTrigger/P2_10_4_0/ntuple_SingleElectron_PU140/SingleE_FlatPt-2to100/CRAB3/190211_021652/0000/ntuple_SingleElectron_PU140_%i.root' % (i+1) for i in xrange(28)]
  pufiles += ['root://cmsxrootd-site.fnal.gov//store/group/l1upgrades/L1MuonTrigger/P2_10_4_0/ntuple_SingleElectron_PU200/SingleE_FlatPt-2to100/CRAB3/190211_182015/0000/ntuple_SingleElectron_PU200_%i.root' % (i+1) for i in xrange(27)]
  #pufiles += ['root://cmsxrootd-site.fnal.gov//store/group/l1upgrades/L1MuonTrigger/P2_10_4_0/ntuple_SingleMuon_PU140/SingleMu_FlatPt-2to100/CRAB3/190211_182130/0000/ntuple_SingleMuon_PU140_%i.root' % (i+1) for i in xrange(25)]
  #pufiles += ['root://cmsxrootd-site.fnal.gov//store/group/l1upgrades/L1MuonTrigger/P2_10_4_0/ntuple_SingleMuon_PU200/SingleMu_FlatPt-2to100/CRAB3/190211_182229/0000/ntuple_SingleMuon_PU200_%i.root' % (i+1) for i in xrange(26)]
  pufiles += ['root://cmsxrootd-site.fnal.gov//store/group/l1upgrades/L1MuonTrigger/P2_10_4_0/ntuple_SinglePhoton_PU140/SinglePhoton_FlatPt-8to150/CRAB3/190211_182324/0000/ntuple_SinglePhoton_PU140_%i.root' % (i+1) for i in xrange(27)]
  pufiles += ['root://cmsxrootd-site.fnal.gov//store/group/l1upgrades/L1MuonTrigger/P2_10_4_0/ntuple_SinglePhoton_PU200/SinglePhoton_FlatPt-8to150/CRAB3/190211_182443/0000/ntuple_SinglePhoton_PU200_%i.root' % (i+1) for i in xrange(27)]

  # For testing purposes (SingleNeutrino, PU200)
  pufiles += ['root://cmsxrootd-site.fnal.gov//store/group/l1upgrades/L1MuonTrigger/P2_10_4_0/ntuple_SingleNeutrino_PU200/SingleNeutrino/CRAB3/190209_121428/0000/ntuple_SingleNeutrino_PU200_%i.root' % (i+1) for i in xrange(30,63)]  # from 30/63

  infile = pufiles[j]
  infile_r = root_open(infile)
  tree = infile_r.ntupler.tree
  print('[INFO] Opening file: %s' % infile)

  # Define collection
  tree.define_collection(name='hits', prefix='vh_', size='vh_size')
  tree.define_collection(name='tracks', prefix='vt_', size='vt_size')
  tree.define_collection(name='particles', prefix='vp_', size='vp_size')
  tree.define_collection(name='evt_info', prefix='ve_', size='ve_size')
  return tree

def unload_tree():
  global infile_r
  try:
    infile_r.close()
  except:
    pass


# ______________________________________________________________________________
# Main

if __name__ == "__main__":
  print('[INFO] Using cmssw     : {0}'.format(os.environ['CMSSW_VERSION']))
  print('[INFO] Using condor    : {0}'.format(use_condor))
  print('[INFO] Using max events: {0}'.format(maxEvents))
  print('[INFO] Using algo      : {0}'.format(algo))
  print('[INFO] Using analysis  : {0}'.format(analysis))
  print('[INFO] Using job id    : {0}'.format(jobid))

  if algo == 'run3':
    run2_input = True
  else:
    run2_input = False

  if algo == 'omtf':
    omtf_input = True
  else:
    omtf_input = False

  if analysis == 'dummy':
    analysis = DummyAnalysis()
    analysis.run(omtf_input=omtf_input, run2_input=run2_input)

  elif analysis == 'roads':
    analysis = RoadsAnalysis()
    analysis.run(omtf_input=omtf_input, run2_input=run2_input)

  elif analysis == 'rates':
    analysis = RatesAnalysis()
    analysis.run(omtf_input=omtf_input, run2_input=run2_input, pileup=200)
  elif analysis == 'rates140':
    analysis = RatesAnalysis()
    analysis.run(omtf_input=omtf_input, run2_input=run2_input, pileup=140)
  elif analysis == 'rates250':
    analysis = RatesAnalysis()
    analysis.run(omtf_input=omtf_input, run2_input=run2_input, pileup=250)
  elif analysis == 'rates300':
    analysis = RatesAnalysis()
    analysis.run(omtf_input=omtf_input, run2_input=run2_input, pileup=300)

  elif analysis == 'effie':
    analysis = EffieAnalysis()
    analysis.run(omtf_input=omtf_input, run2_input=run2_input)

  elif analysis == 'mixing':
    analysis = MixingAnalysis()
    analysis.run(omtf_input=omtf_input, run2_input=run2_input)

  else:
    raise RunTimeError('Cannot recognize analysis: {0}'.format(analysis))
