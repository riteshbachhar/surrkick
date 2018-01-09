''' Extract black hole kicks from gravitational-wave surrogate models. '''

from __future__ import print_function,division
import sys
import os
import time
import numpy as np
import scipy.integrate
import scipy.optimize
import scipy.stats
from scipy.interpolate import InterpolatedUnivariateSpline as spline
import NRSur7dq2
from singleton_decorator import singleton
import h5py
from tqdm import tqdm
import cPickle as pickle
import multiprocessing, pathos.multiprocessing
import precession
import warnings

__author__ = "Davide Gerosa"
__email__ = "dgerosa@caltech.edu"
__license__ = "MIT"
__version__ = "0.0"
__doc__="**Author** "+__author__+"\n\n"+\
        "**email** "+__email__+"\n\n"+\
        "**Licence** "+__license__+"\n\n"+\
        "**Version** "+__version__+"\n\n"+\
        __doc__


class summodes(object):
    '''Return indexes to perform a mode sum in l (from 0 to lmax) and m (from -l to +l).'''

    @staticmethod
    def single(lmax):
        '''Single mode sum: Sum_{l,m} with 0<=l<=lmax and -l<=m<=l. Returns a list of (l,m) tuples.
        Usage: iterations=surrkick.summodes.single(lmax)'''

        iterations=[]
        for l1 in np.arange(2,lmax+1):
            for m1 in np.arange(-l1,l1+1):
                    iterations.append((l1,m1))
        return iterations

    @staticmethod
    def double(lmax):
        '''Double mode sum: Sum_{l1,m1} Sum_{l2,m2}  with 0<=l1,l2<=lmax and -l<=m<=l. Returns a list of (l1,m1,l2,m2) tuples.
        Usage: iterations=surrkick.summodes.double(lmax)'''


        iterations=[]
        for l1 in np.arange(2,lmax+1):
            for m1 in np.arange(-l1,l1+1):
                for l2 in np.arange(2,lmax+1):
                    for m2 in np.arange(-l2,l2+1):
                        iterations.append((l1,m1,l2,m2))

        return iterations


class coeffs(object):
    '''Coefficients of the momentum expression, from Eqs. (3.16-3.19,3.25) of arXiv:0707.4654. All are defined as static methods and can be called with, e.g., coeffs.a(l,m).'''

    @staticmethod
    def a(l,m):
        '''Eq. (3.16) of arXiv:0707.4654.
        Usage: a=surrkick.coeffs.a(l,m)'''

        return ( (l-m) * (l+m+1) )**0.5 / ( l * (l+1) )

    @staticmethod
    def b(l,m):
        '''Eq. (3.17) of arXiv:0707.4654.
        Usage: b=surrkick.coeffs.b(l,m)'''

        return  ( 1/(2*l) ) *  ( ( (l-2) * (l+2) * (l+m) * (l+m-1) ) / ( (2*l-1) * (2*l+1) ))**0.5

    @staticmethod
    def c(l,m):
        '''Eq. (3.18) of arXiv:0707.4654.
        Usage: c=surrkick.coeffs.c(l,m)'''

        return  2*m / ( l * (l+1) )

    @staticmethod
    def d(l,m):
        '''Eq. (3.19) of arXiv:0707.4654.
        Usage: d=surrkick.coeffs.d(l,m)'''

        return  ( 1/l ) *  ( ( (l-2) * (l+2) * (l-m) * (l+m) ) / ( (2*l-1) * (2*l+1) ))**0.5

    @staticmethod
    def f(l,m):
        '''Eq. (3.25) of arXiv:0707.4654.
        Usage: `f=surrkick.coeffs.f(l,m)`'''

        return  ( l*(l+1) - m*(m+1) )**0.5



class convert(object):
    '''Utility class to convert units to other units.'''

    @staticmethod
    def kms(x):
        '''Convert a velocity from natural units (c=1) to km/s.
        Usage: vkms=surrkick.convert.kms(vnat)'''

        return x * 299792.458

    @staticmethod
    def cisone(x):
        '''Convert a velocity from km/s to natural units (c=1)
        Usage: vnat=surrkick.convert.cisone(vkms)'''

        return x / 299792.458

@singleton
class surrogate(object):
    '''Initialize the NRSur7dq2 surrogate model described in arXiv:1705.07089. The only purpose of this class is to wrap the surrogate with a singleton pattern (which means there can only be one instance of this class, and creation of additional instances just return the first one).'''

    def __init__(self):
        '''Placeholder'''
        self._sur=None

    def sur(self):
        '''Load surrogate from file NRSur7dq2.h5.
        Usage: sur=surrkick.surrogate().sur()'''

        if self._sur==None:
            self._sur= NRSur7dq2.NRSurrogate7dq2()
            #self._sur = NRSur7dq2.NRSurrogate7dq2('NRSur7dq2.h5')
        return self._sur


class surrkick(object):
    '''Extract energy, linear momentum and angular momentum emitted in gravitational waves from a waveform surrogate model. We use a frame where the orbital angular momentum is along the z axis and the heavier (lighter) is on the positive (negative) x-axis at the reference time `t_ref`.
    Usage: sk=surrkick.surrkick(q=1,chi1=[0,0,0],chi2=[0,0,0],t_ref=-100)
    Parameters:
    - `q`: binary mass ratio in the range 1:1 to 2:1. Can handle both conventions: q in [0.5,1] or [1,2].
    - `chi1`: spin vector of the heavier BH.
    - `chi2`: spin vector of the lighter BH.
    - `t_ref`: reference time at which spins are specified (must be -4500<=t_ref<=-100)
    '''

    def __init__(self,q=1,chi1=[0,0,0],chi2=[0,0,0],t_ref=-100):
        '''Initialize the `surrkick` class.'''

        self.sur=surrogate().sur()
        '''Initialize the surrogate. Note it's a singleton'''

        self.q = max(q,1/q)
        '''Binary mass ratio in the range 1:1 to 2:1.
        Usage: q=surrkick.surrkick().q'''

        self.chi1 = np.array(chi1)
        '''Spin vector of the heavier BH.
        Usage: chi1=surrkick.surrkick().chi1'''

        self.chi2 = np.array(chi2)
        '''Spin vector of the lighter BH.
        Usage: chi2=surrkick.surrkick().chi2'''

        self.times = self.sur.t_coorb
        '''Time nodes where quantities are evaluated.
        Usage: times=surrkick.surrkick().times'''

        # Check the reference time makes sense
        self.t_ref=t_ref
        '''Reference time at which spins are specified (must be -4500<=t_ref<=-100).
        Usage: t_ref=surrkick.surrkick().t_ref'''

        assert self.t_ref>=-4500 and self.t_ref<=-100 # Check you're in the regions where spins are OK.
        if t_ref==-4500: # This is the default value for NRSur7dq2, which wants a None
            self.t_ref = None

        # Hidden variables for lazy loading
        self._hsample = None
        self._hdotsample = None
        self._lmax = None
        self._dEdt = None
        self._Eoft = None
        self._Erad = None
        self._dPdt = None
        self._Poft = None
        self._Prad = None
        self._dJdt = None
        self._Joft = None
        self._Jrad = None
        self._xoft = None


    @property
    def hsample(self):
        '''Modes of the gravitational-wave strain h=hp-i*hc evaluated at the surrogate time nodes. Returns a dictiornary with keys (l,m).
        Usage: hsample=surrkick.surrkick().hsample; hsample[l,m]'''

        if self._hsample is None:
            self._hsample = self.sur(self.q, self.chi1, self.chi2,t=self.times,t_ref=self.t_ref) # Returns a python dictionary with keys (l,m)
        return self._hsample

    @property
    def lmax(self):
        '''Largest l mode available. lmax=4 for NRSur7dq2.
        Usage: lmax=surrkick.surrkick().lmax'''

        if self._lmax is None:
            self._lmax = sorted(self.hsample.keys())[-1][0]
            #self._lmax=3 # To cut all extractions at l=3
        return self._lmax

    def h(self,l,m):
        '''Correct the strain values to return zero if either l or m are not allowed (this is how the expressions of arXiv:0707.4654 are supposed to be used). Returns a single mode.
        Usage: hlm=surrkick.surrkick().h(l,m)'''

        if l<2 or l>self.lmax:
            return np.zeros(len(self.times),dtype=complex)
        elif m<-l or m>l:
            return np.zeros(len(self.times),dtype=complex)
        else:
            return self.hsample[l,m]

    @property
    def hdotsample(self):
        '''Derivative of the strain at the time nodes. First interpolate the h with a standard spline, then derivate the spline and evaluate that derivative at the time nodes. Returns a dictiornary with keys (l,m).
        Usage: hdotsample=surrkick.surrkick().hdotsample; hdotsample[l,m]'''

        if self._hdotsample is None:

            # Derivatives with splines
            self._hdotsample =  {k: spline(self.times,v.real).derivative()(self.times)+1j*spline(self.times,v.imag).derivative()(self.times) for k, v in self.hsample.items()}

            # Derivatives with finite differencing
            #self._hdotsample = {k: np.gradient(v,edge_order=2)/np.gradient(self.times,edge_order=2) for k, v in self.hsample.items()}


        return self._hdotsample

    def hdot(self,l,m):
        '''Correct the hdot values to return zero if either l or m are not allowed (this is how the expressions of arXiv:0707.4654 are supposed to be used). Returns a single mode.
        Usage: hdotlm=surrkick.surrkick().hdot(l,m)'''

        if l<2 or l>self.lmax:
            return np.zeros(len(self.times),dtype=complex)
        elif m<-l or m>l:
            return np.zeros(len(self.times),dtype=complex)
        else:
            return self.hdotsample[l,m]

    @property
    def dEdt(self):
        '''Implement Eq. (3.8) of arXiv:0707.4654 for the energy momentum flux. Note that the modes provided by the surrogate models are actually h*(r/M) extracted as r=infinity, so the r^2 factor is already in there.
        Usage: dEdt=surrkick.surrkick().dEdt
        '''

        if self._dEdt is None:
            dEdt = 0
            for l,m in summodes.single(self.lmax):

                # Eq. (3.8)
                dEdt += (1/(16*np.pi)) * np.abs(self.hdot(l,m))**2

            self._dEdt = dEdt

        return self._dEdt

    @property
    def Eoft(self):
        '''Radiated energy as a function of time. Time integral of Eq. (3.8) of arXiv:0707.4654, evaluated at the time nodes. We first interpolate with a spline and then integrate analytically.
        Usage: Eoft=surrkick.surrkick().Eoft'''

        if self._Eoft is None:
            # The integral of a spline is called antiderivative (mmmh...)
            origin=0
            self._Eoft = spline(self.times,self.dEdt).antiderivative()(self.times)-origin
        return self._Eoft

    @property
    def Erad(self):
        ''' Total energy radiated, i.e. E(t) at the last time node.
        Usage: Erad=surrkick.surrkick().Erad'''

        if self._Erad is None:
            self._Erad = self.Eoft[-1]
        return self._Erad

    @property
    def dPdt(self):
        '''Implement Eq. (3.14-3.15) of arXiv:0707.4654 for the three component of the linear momentum momentum flux. Note that the modes provided by the surrogate models are actually h*(r/M) extracted as r=infinity, so the r^2 factor is already in there. Returned array has size len(times)x3.
        Usage: dPdt=surrkick.surrkick().dPdt'''

        if self._dPdt is None:

            dPpdt = 0
            dPzdt = 0

            for l,m in summodes.single(self.lmax):

                # Eq. 3.14. dPpdt= dPxdt + i dPydt
                dPpdt += (1/(8*np.pi)) * self.hdot(l,m) * ( coeffs.a(l,m) * np.conj(self.hdot(l,m+1)) + coeffs.b(l,-m) * np.conj(self.hdot(l-1,m+1)) - coeffs.b(l+1,m+1) * np.conj(self.hdot(l+1,m+1)) )
                # Eq. 3.15
                dPzdt += (1/(16*np.pi)) * self.hdot(l,m) * ( coeffs.c(l,m) * np.conj(self.hdot(l,m)) + coeffs.d(l,m) * np.conj(self.hdot(l-1,m)) + coeffs.d(l+1,m) * np.conj(self.hdot(l+1,m)) )

            dPxdt=dPpdt.real # From the definition of Pplus
            dPydt=dPpdt.imag # From the definition of Pplus

            assert max(dPzdt.imag)<1e-6 # Check...
            dPzdt=dPzdt.real # Kill the imaginary part

            self._dPdt = np.transpose([dPxdt,dPydt,dPzdt])

        return self._dPdt

    @property
    def Poft(self):
        '''Radiated linear momentum as a function of time. Time integral of Eq. (3.14-3.15) of arXiv:0707.4654, evaluated at the time nodes. We first interpolate with a spline and then integrate analytically.
        Usage: Poft=surrkick.surrkick().Poft'''

        if self._Poft is None:
            # The integral of a spline is called antiderivative (mmmh...)
            origin=[0,0,0]
            self._Poft = np.transpose([spline(self.times,v).antiderivative()(self.times)-o  for v,o in zip(np.transpose(self.dPdt),origin)])

            # Eliminate unphysical drift due to the starting point of the integration. Integrate for tbuffer and substract the mean.
            tbuffer=-1000
            tend=self.times[0]-tbuffer
            P0 = np.array([spline(self.times[self.times<tend],v[self.times<tend]).antiderivative()(tend)/tbuffer  for v in np.transpose(self.Poft)])

        return self._Poft

    @property
    def Prad(self):
        '''Total linear momentum radiated, i.e. the norm of [Px(t),Py(t),Pz(t)] at the last time node.
        Usage: Prad=surrkick.surrkick().Prad'''

        if self._Prad is None:
            self._Prad = np.linalg.norm(self.Poft[-1])
        return self._Prad

    @property
    def voft(self):
        '''Velocity of the center of mass, i.e. minus the momentum radiated.
        Usage: voft=surrkick.surrkick().voft'''

        return -self.Poft

    @property
    def kick(self):
        '''Final kick velocity, equal to the total linear momentum radiated.
        Usage: kick=surrkick.surrkick().kick'''

        return self.Prad

    @property
    def kickcomp(self):
        '''Components of the final kick in the xyz frame of the surrogate
        Usage: vkx,vky,vkz=surrkick.surrkick().kick'''

        return self.voft[-1]

    @property
    def kickdir(self):
        '''Direction of the final kick in the xyz frame of the surrogate
        Usage: vkhatx,vkhaty,vkhatz=surrkick.surrkick().kick'''

        return self.kickcomp/self.kick


    @property
    def dJdt(self):
        '''Implement Eq. (3.22-3.24) of arXiv:0707.4654 for the three component of the angular momentum momentum flux. Note that the modes provided by the surrogate models are actually h*(r/M) extracted as r=infinity, so the r^2 factor is already in there. Returned array has size len(times)x3.
        Usage: dPdt=surrkick.surrkick().dPdt'''

        if self._dJdt is None:

            dJxdt = 0
            dJydt = 0
            dJzdt = 0

            for l,m in summodes.single(self.lmax):

                # Eq. 3.22
                dJxdt += (1/(32*np.pi)) * self.h(l,m) * ( coeffs.f(l,m) * np.conj(self.hdot(l,m+1)) + coeffs.f(l,-m) * np.conj(self.hdot(l,m-1)) )
                # Eq. 3.23
                dJydt += (-1/(32*np.pi)) * self.h(l,m) * ( coeffs.f(l,m) * np.conj(self.hdot(l,m+1)) - coeffs.f(l,-m) * np.conj(self.hdot(l,m-1)) )
                # Eq. 3.24
                dJzdt += (1/(16*np.pi)) * m * self.h(l,m) * np.conj(self.hdot(l,m))

            dJxdt=dJxdt.imag
            dJydt=dJydt.real
            dJzdt=dJzdt.imag


            self._dJdt = np.transpose([dJxdt,dJydt,dJzdt])

        return self._dJdt

    @property
    def Joft(self):
        '''Radiated angular momentum as a function of time. Time integral of Eq. (3.22-3.24) of arXiv:0707.4654, evaluated at the time nodes. We first interpolate with a spline and then integrate analytically.
        Usage: Joft=surrkick.surrkick().Joft'''

        if self._Joft is None:

            # The integral of a spline is called antiderivative (mmmh...)
            origin=[0,0,0]
            self._Joft = np.transpose([spline(self.times,v).antiderivative()(self.times)-o  for v,o in zip(np.transpose(self.dJdt),origin)])

        return self._Joft

    @property
    def Jrad(self):
        '''Total angular momentum radiated, i.e. the norm of [Jx(t),Jy(t),Jz(t)] at the last time node.
        Usage: Jrad=surrkick.surrkick().Jrad'''

        if self._Jrad is None:
            self._Jrad = np.linalg.norm(self.Joft[-1])
        return self._Jrad

    @property
    def xoft(self):
        '''Trajectory of the spacetime center of mass, i.e. time integral of -P(t), where P is the linear momentum radiated in GWs.
        Usage: xoft=surrkick.surrkick().xoft'''

        if self._xoft is None:
            # The integral of a spline is called antiderivative (mmmh...)
            origin=[0,0,0]
            self._xoft = np.transpose([spline(self.times,v).antiderivative()(self.times)-vo*self.times  for v,vo in zip(np.transpose(self.voft),origin)])
        return self._xoft



def project(timeseries,direction):
    '''Project a 3D time series along some direction.
    Usage projection=project(timeseries, direction)'''

    return np.array([np.dot(t,direction) for t in timeseries])


class plots(object):
    '''Produce plots of our paper FIX_PAPER_REF.'''

    def plottingstuff(function):
        '''Python decorator to handle plotting, including defining all defaults and storing the final pdf. Just add @plottingstuff to any methond of the plots class.'''

        def wrapper(self):
            print("Plotting:", function.__name__+".pdf")

            # Before function call
            global plt,AutoMinorLocator,MultipleLocator,LogLocator,NullFormatter
            from matplotlib import use #Useful when working on SSH
            use('Agg')
            from matplotlib import rc
            font = {'family':'serif','serif':['cmr10'],'weight' : 'medium','size' : 16}
            rc('font', **font)
            rc('text',usetex=True)
            #rc.rcParams['text.latex.preamble']=[r"\usepackage{amsmath}"]
            #rc('text.latex',preamble=r"\usepackage{amsmath}")
            import matplotlib
            matplotlib.rcParams['text.latex.preamble']=[r"\usepackage{amsmath}"]
            rc('figure',max_open_warning=1000)
            rc('xtick',top=True)
            rc('ytick',right=True)
            import matplotlib.pyplot as plt
            from mpl_toolkits.mplot3d import Axes3D
            from matplotlib.backends.backend_pdf import PdfPages
            from matplotlib.ticker import AutoMinorLocator,MultipleLocator,LogLocator,NullFormatter
            pp= PdfPages(function.__name__+".pdf")

            fig = function(self)
            # Handle multiple figures
            try:
                len(fig)
            except:

                fig=[fig]
            for f in tqdm(fig):

                # Filter our annoying "elementwise comparison failed" warning (something related to the matplotlib backend and future versions)
                with warnings.catch_warnings():
                    warnings.simplefilter(action='ignore', category=FutureWarning)

                    f.savefig(pp, format='pdf',bbox_inches='tight')
                f.clf()
            pp.close()
        return wrapper


    def animate(function):
        '''Python decorator to handle animations, including defining all defaults and storing the final movie. Just add @animate to any methond of the plots class.'''

        #def wrapper(*args, **kw):
        def wrapper(self):
            print("Animation:", function.__name__+".mpg/h264")


            # Before function call
            global plt,AutoMinorLocator,MultipleLocator
            from matplotlib import use #Useful when working on SSH
            use('Agg')
            from matplotlib import rc
            font = {'family':'serif','serif':['cmr10'],'weight' : 'medium','size' : 16}
            rc('font', **font)
            rc('text',usetex=True)
            import matplotlib
            matplotlib.rcParams['text.latex.preamble']=[r"\usepackage{amsmath}"]
            matplotlib.rcParams['figure.max_open_warning']=int(1e4)
            rc('figure',max_open_warning=100000)
            rc('xtick',top=True)
            rc('ytick',right=True)
            import matplotlib.pyplot as plt
            from mpl_toolkits.mplot3d import Axes3D
            from matplotlib.backends.backend_pdf import PdfPages
            from matplotlib.ticker import AutoMinorLocator,MultipleLocator

            figs = function(self)
            try:
                len(figs[0])
            except:
                figs=[figs]

            for j,fig in enumerate(tqdm(figs)):
                print(j)

                if fig != [None]:

                    for i,f in enumerate(tqdm(fig)):

                        with warnings.catch_warnings():
                            warnings.simplefilter(action='ignore', category=FutureWarning)
                            framename = function.__name__+"_"+str(j)+"_"+"%05d.png"%i
                            f.savefig(framename, bbox_inches='tight',format='png',dpi = 300)
                            f.clf()

                    rate = 100 #The movie is faster if this number is large
                    command ='ffmpeg -r '+str(rate)+' -i '+function.__name__+'_'+str(j)+'_'+'%05d.png -vcodec libx264 -crf 18 -y -an '+function.__name__+'_'+str(j)+'.mp4 -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2"'
                    print(command)
                    os.system(command)

                    if False:
                        command="rm -f "+function.__name__+"_"+str(j)+"*.png temp"
                        os.system(command)


        return wrapper





    @classmethod
    @plottingstuff
    def nospinprofiles(self):
        '''Fig. FIX_PAPER_FIG of FIX_PAPER_REF.'''

        L=0.7
        H=0.3
        S=0.05

        figP = plt.figure(figsize=(6,6))
        axP = [figP.add_axes([0,-i*(S+H),L,H]) for i in [0,1,2,3]]
        figE = plt.figure(figsize=(6,6))
        axE = figE.add_axes([0,0,L,H])
        figJ = plt.figure(figsize=(6,6))
        axJ = [figJ.add_axes([0,-i*(S+H),L,H]) for i in [0,1,2,3]]

        q_vals = np.linspace(1,0.5,8)
        for i,q in enumerate(tqdm(q_vals)):
            color=plt.cm.copper(i/len(q_vals))
            b = surrkick(q=q)
            dashes=''
            axP[0].plot(b.times,b.voft[:,0]*1000,color=color,alpha=0.9,dashes=dashes)
            axP[1].plot(b.times,b.voft[:,1]*1000,color=color,alpha=0.9,dashes=dashes)
            axP[2].plot(b.times,b.voft[:,2]*1000,color=color,alpha=0.9,dashes=dashes)
            axP[3].plot(b.times,project(b.voft,b.kickdir)*1000,color=color,alpha=0.9,dashes=dashes)
            axE.plot(b.times,b.Eoft,color=color,alpha=0.7)
            axJ[0].plot(b.times,b.Joft[:,0],color=color,alpha=0.7,dashes=dashes)
            axJ[1].plot(b.times,b.Joft[:,1],color=color,alpha=0.7,dashes=dashes)
            axJ[2].plot(b.times,b.Joft[:,2],color=color,alpha=0.7,dashes=dashes)
            axJ[3].plot(b.times,project(b.Joft,b.Joft[-1]),color=color,alpha=0.7,dashes=dashes)

        for ax in axP+[axE]+axJ:
            ax.set_xlim(-50,50)
            ax.xaxis.set_minor_locator(AutoMinorLocator())
            ax.yaxis.set_minor_locator(MultipleLocator(0.25))
        for ax in axP[:-1]+axJ[:-1]:
            ax.set_xticklabels([])
        for ax in [axP[-1]]+[axJ[-1]]+[axE]:
            ax.set_xlabel("$t\;\;[M]$")
        for ax,d in zip(axP,["x","y","z","v_k"]):
            ax.set_ylim(-1,1)
            ax.set_ylabel("$\mathbf{v}(t) \cdot \mathbf{\hat "+d+"} \;\;[0.001c]$")
        axE.set_ylabel("$E(t) \;\;[M]$")
        for ax,d in zip(axJ,["x","y","z","J_k"]):
            ax.set_ylim(-0.1,0.5)
            ax.set_ylabel("$\mathbf{J}(t) \cdot \mathbf{\hat "+d+"} \;\;[0.001c]$")
        axP[0].text(0.05,0.7,'$q=0.5\,...\,1$\n$\chi_1=\chi_2=0$',transform=axP[0].transAxes,linespacing=1.4)

        #return [figP,figE,figJ]
        return figP


    @classmethod
    @plottingstuff
    def centerofmass(self):
        '''Fig. FIX_PAPER_FIG of FIX_PAPER_REF.'''

        allfig=[]

        # Left panel
        if True:

            fig = plt.figure(figsize=(6,6))
            ax=fig.add_axes([0,0,0.7,0.7], projection='3d')

            q=0.5
            chi1=[0,0,0]
            chi2=[0,0,0]

            sk = surrkick(q=q , chi1=chi1,chi2=chi2)
            x0,y0,z0=sk.xoft[sk.times==min(abs(sk.times))][0]
            x,y,z=np.transpose(sk.xoft[np.logical_and(sk.times>-4000,sk.times<26)])
            ax.plot(x-x0,y-y0,z-z0)
            ax.scatter(0,0,0,marker='.',s=60,alpha=0.5)
            x,y,z=np.transpose(sk.xoft)
            vx,vy,vz=np.transpose(sk.voft)

            for t in [-10,3,12,17,24]:
                i = np.abs(sk.times - t).argmin()
                v=np.linalg.norm([vx[i],vy[i],vz[i]])
                arrowsize=1e-4
                ax.quiver(x[i]-x0,y[i]-y0,z[i]-z0,vx[i]*arrowsize/v,vy[i]*arrowsize/v,vz[i]*arrowsize/v,length=0.0001,arrow_length_ratio=50000,alpha=0.5)

            ax.set_xlim(-0.004,0.0045)
            ax.set_ylim(-0.0025,0.006)
            ax.set_zlim(-0.006,0.0035)
            ax.set_xticklabels(ax.get_xticks(), fontsize=9)
            ax.set_yticklabels(ax.get_yticks(), fontsize=9)
            ax.set_zticklabels(ax.get_zticks(), fontsize=9)
            fig.text(0.38,0.45,'$q='+str(q)+'$\n$\\chi_1=0$\n${\\chi_2}=0$\n$v_k='+str(int(convert.kms(sk.kick)))+'{\\rm \;km/s}$',transform=fig.transFigure)
            ax.set_xlabel("$x\;\;[M]$",fontsize=13)
            ax.set_ylabel("$y\;\;[M]$",fontsize=13)
            ax.set_zlabel("$z\;\;[M]$",fontsize=13)
            ax.xaxis.labelpad=6
            ax.yaxis.labelpad=8
            ax.zaxis.labelpad=4
            ax.xaxis.set_minor_locator(AutoMinorLocator())
            ax.yaxis.set_minor_locator(AutoMinorLocator())
            ax.zaxis.set_minor_locator(AutoMinorLocator())

            allfig.append(fig)

        # Middle panel
        if True:

            fig = plt.figure(figsize=(6,6))
            ax=fig.add_axes([0,0,0.7,0.7], projection='3d')

            q=0.5
            chi1=[0.8,0,0]
            chi2=[-0.8,0,0]

            sk = surrkick(q=q , chi1=chi1, chi2=chi2)
            x0,y0,z0=sk.xoft[sk.times==min(abs(sk.times))][0]
            x,y,z=np.transpose(sk.xoft[np.logical_and(sk.times>-3500,sk.times<16)])
            ax.plot(x-x0,y-y0,z-z0)
            ax.scatter(0,0,0,marker='.',s=40,alpha=0.5)
            x,y,z=np.transpose(sk.xoft)
            vx,vy,vz=np.transpose(sk.voft)

            for t in [-20,-3,3,3,10,14]:
                i = np.abs(sk.times - t).argmin()
                v=np.linalg.norm([vx[i],vy[i],vz[i]])
                arrowsize=2e-3
                ax.quiver(x[i]-x0,y[i]-y0,z[i]-z0,vx[i]*arrowsize/v,vy[i]*arrowsize/v,vz[i]*arrowsize/v,length=0.00001,arrow_length_ratio=30000,alpha=0.5)

            ax.set_xlim(-0.005,0.005)
            ax.set_ylim(-0.005,0.005)
            ax.set_zlim(-0.005,0.005)
            ax.set_xticklabels(ax.get_xticks(), fontsize=9)
            ax.set_yticklabels(ax.get_yticks(), fontsize=9)
            ax.set_zticklabels(ax.get_zticks(), fontsize=9)
            fig.text(0.1,0.4,'$q='+str(q)+'$\n$\\boldsymbol{\\chi_1}=[0.8,0,0]$\n$\\boldsymbol{\\chi_2}=[-0.8,0,0]$\n$v_k='+str(int(convert.kms(sk.kick)))+'{\\rm \;km/s}$',transform=fig.transFigure)
            ax.set_xlabel("$x\;\;[M]$",fontsize=13)
            ax.set_ylabel("$y\;\;[M]$",fontsize=13)
            ax.set_zlabel("$z\;\;[M]$",fontsize=13)
            ax.xaxis.labelpad=6
            ax.yaxis.labelpad=8
            ax.zaxis.labelpad=4
            ax.xaxis.set_minor_locator(AutoMinorLocator())
            ax.yaxis.set_minor_locator(AutoMinorLocator())
            ax.zaxis.set_minor_locator(AutoMinorLocator())

            allfig.append(fig)

        # Right panel
        if True:

            fig = plt.figure(figsize=(6,6))
            ax=fig.add_axes([0,0,0.7,0.7], projection='3d')

            q=1
            chi1=[0.81616392, 0.01773234, 0.57754829]
            chi1=np.array(chi1)*0.8/np.linalg.norm(chi1)
            chi2=[-0.87810809, 0.06485156, 0.47404689]
            chi2=np.array(chi2)*0.8/np.linalg.norm(chi2)

            sk = surrkick(q=q , chi1=chi1, chi2=chi2)
            x0,y0,z0=sk.xoft[sk.times==min(abs(sk.times))][0]
            x,y,z=np.transpose(sk.xoft[np.logical_and(sk.times>-4500,sk.times<7.2)])
            ax.plot(x-x0,y-y0,z-z0)
            ax.scatter(0,0,0,marker='.',s=40,alpha=0.5)
            x,y,z=np.transpose(sk.xoft)
            vx,vy,vz=np.transpose(sk.voft)

            for t in [-9,3,4.5]:
                i = np.abs(sk.times - t).argmin()
                v=np.linalg.norm([vx[i],vy[i],vz[i]])
                arrowsize=2e-3
                ax.quiver(x[i]-x0,y[i]-y0,z[i]-z0,vx[i]*arrowsize/v,vy[i]*arrowsize/v,vz[i]*arrowsize/v,length=0.00001,arrow_length_ratio=30000,alpha=0.5)

            ax.set_xlim(-0.006,0.005)
            ax.set_ylim(-0.006,0.005)
            ax.set_zlim(0,0.011)
            ax.set_xticklabels(ax.get_xticks(), fontsize=9)
            ax.set_yticklabels(ax.get_yticks(), fontsize=9)
            ax.set_zticklabels(ax.get_zticks(), fontsize=9)
            fig.text(0.09,0.45,'$q='+str(q)+'$\n$\\boldsymbol{\\chi_1}=['+
                str(round(chi1[0],2))+','+str(round(chi1[1],2))+','+str(round(chi1[2],2))+
                ']$\n$\\boldsymbol{\\chi_2}=['+
                str(round(chi2[0],2))+','+str(round(chi2[1],2))+','+str(round(chi2[2],2))+
                ']$\n$v_k='+str(int(convert.kms(sk.kick)))+'{\\rm \;km/s}$',transform=fig.transFigure)
            ax.set_xlabel("$x\;\;[M]$",fontsize=13)
            ax.set_ylabel("$y\;\;[M]$",fontsize=13)
            ax.set_zlabel("$z\;\;[M]$",fontsize=13)
            ax.xaxis.labelpad=6
            ax.yaxis.labelpad=8
            ax.zaxis.labelpad=4

            allfig.append(fig)

        return allfig


    @classmethod
    @animate
    def recoil(self):
        '''Fig. FIX_PAPER_FIG of FIX_PAPER_REF.'''

        leftpanel,middlepanel,rightpanel=True,True,True
        #leftpanel,middlepanel,rightpanel=True,False,False
        #leftpanel,middlepanel,rightpanel=False,True,False
        #leftpanel,middlepanel,rightpanel=False,False,True

        allfig=[]

        tnew=np.linspace(-4500,100,4601)
        tnew=np.append(tnew,np.ones(100)*tnew[-1])

        # Left panel
        if leftpanel:

            q=0.5
            chi1=[0,0,0]
            chi2=[0,0,0]
            sk = surrkick(q=q , chi1=chi1,chi2=chi2)
            x0,y0,z0=sk.xoft[sk.times==min(abs(sk.times))][0]
            x,y,z=np.transpose(sk.xoft)
            xnew=spline(sk.times,x)(tnew)
            ynew=spline(sk.times,y)(tnew)
            znew=spline(sk.times,z)(tnew)
            tmax=100 # Fix annyoing bug in matplotlib.axes3d (comes from a 2d backend)
            temp=xnew[tnew<tmax]
            xnew=np.append(temp,temp[-1]*np.ones(len(xnew[tnew>=tmax])))
            temp=ynew[tnew<tmax]
            ynew=np.append(temp,temp[-1]*np.ones(len(ynew[tnew>=tmax])))
            temp=znew[tnew<tmax]
            znew=np.append(temp,temp[-1]*np.ones(len(znew[tnew>=tmax])))

            def _recoil(tilltime):
                fig = plt.figure(figsize=(6,6))
                ax=fig.add_axes([0,0,0.7,0.7], projection='3d')
                fig.text(0.25,0.67,'$t='+str(int(tilltime))+'M$',transform=fig.transFigure, horizontalalignment='left',verticalalignment='bottom')
                x=xnew[tnew<tilltime]
                y=ynew[tnew<tilltime]
                z=znew[tnew<tilltime]
                ax.plot(x-x0,y-y0,z-z0)
                if tilltime>0:
                    ax.scatter(0,0,0,marker='.',s=60,alpha=0.5)
                x,y,z=np.transpose(sk.xoft)
                vx,vy,vz=np.transpose(sk.voft)
                ax.set_xlim(-0.004,0.0045)
                ax.set_ylim(-0.0025,0.006)
                ax.set_zlim(-0.006,0.0035)
                ax.set_xticklabels(ax.get_xticks(), fontsize=9)
                ax.set_yticklabels(ax.get_yticks(), fontsize=9)
                ax.set_zticklabels(ax.get_zticks(), fontsize=9)
                fig.text(0.38,0.45,'$q='+str(q)+'$\n$\\chi_1=0$\n${\\chi_2}=0$\n$v_k='+str(int(convert.kms(sk.kick)))+'{\\rm \;km/s}$',transform=fig.transFigure)
                ax.set_xlabel("$x\;\;[M]$",fontsize=13)
                ax.set_ylabel("$y\;\;[M]$",fontsize=13)
                ax.set_zlabel("$z\;\;[M]$",fontsize=13)
                ax.xaxis.labelpad=6
                ax.yaxis.labelpad=8
                ax.zaxis.labelpad=4
                ax.xaxis.set_minor_locator(AutoMinorLocator())
                ax.yaxis.set_minor_locator(AutoMinorLocator())
                ax.zaxis.set_minor_locator(AutoMinorLocator())
                return fig

            figs=[_recoil(t) for t in tqdm(tnew)]
            allfig.append(figs)

        else:
            allfig.append([None])

        # Middle panel
        if middlepanel:

            q=0.5
            chi1=[0.8,0,0]
            chi2=[-0.8,0,0]
            sk = surrkick(q=q , chi1=chi1,chi2=chi2)
            x0,y0,z0=sk.xoft[sk.times==min(abs(sk.times))][0]
            x,y,z=np.transpose(sk.xoft)
            xnew=spline(sk.times,x)(tnew)
            ynew=spline(sk.times,y)(tnew)
            znew=spline(sk.times,z)(tnew)
            tmax=50 # Fix annyoing bug in matplotlib.axes3d (comes from a 2d backend)
            temp=xnew[tnew<tmax]
            xnew=np.append(temp,temp[-1]*np.ones(len(xnew[tnew>=tmax])))
            temp=ynew[tnew<tmax]
            ynew=np.append(temp,temp[-1]*np.ones(len(ynew[tnew>=tmax])))
            temp=znew[tnew<tmax]
            znew=np.append(temp,temp[-1]*np.ones(len(znew[tnew>=tmax])))

            def _recoil(tilltime):
                fig = plt.figure(figsize=(6,6))
                ax=fig.add_axes([0,0,0.7,0.7], projection='3d')
                fig.text(0.25,0.67,'$t='+str(int(tilltime))+'M$',transform=fig.transFigure, horizontalalignment='left',verticalalignment='bottom')
                x=xnew[tnew<tilltime]
                y=ynew[tnew<tilltime]
                z=znew[tnew<tilltime]
                ax.plot(x-x0,y-y0,z-z0)
                if tilltime>0:
                    ax.scatter(0,0,0,marker='.',s=60,alpha=0.5)
                x,y,z=np.transpose(sk.xoft)
                vx,vy,vz=np.transpose(sk.voft)
                ax.set_xlim(-0.005,0.005)
                ax.set_ylim(-0.005,0.005)
                ax.set_zlim(-0.005,0.005)
                ax.set_xticklabels(ax.get_xticks(), fontsize=9)
                ax.set_yticklabels(ax.get_yticks(), fontsize=9)
                ax.set_zticklabels(ax.get_zticks(), fontsize=9)
                fig.text(0.1,0.4,'$q='+str(q)+'$\n$\\boldsymbol{\\chi_1}=[0.8,0,0]$\n$\\boldsymbol{\\chi_2}=[-0.8,0,0]$\n$v_k='+str(int(convert.kms(sk.kick)))+'{\\rm \;km/s}$',transform=fig.transFigure)
                ax.set_xlabel("$x\;\;[M]$",fontsize=13)
                ax.set_ylabel("$y\;\;[M]$",fontsize=13)
                ax.set_zlabel("$z\;\;[M]$",fontsize=13)
                ax.xaxis.labelpad=6
                ax.yaxis.labelpad=8
                ax.zaxis.labelpad=4
                ax.xaxis.set_minor_locator(AutoMinorLocator())
                ax.yaxis.set_minor_locator(AutoMinorLocator())
                ax.zaxis.set_minor_locator(AutoMinorLocator())
                return fig

            figs=[_recoil(t) for t in tqdm(tnew)]
            allfig.append(figs)

        else:
            allfig.append([None])

        # Right panel
        if rightpanel:

            q=1
            chi1=[0.81616392, 0.01773234, 0.57754829]
            chi1=np.array(chi1)*0.8/np.linalg.norm(chi1)
            chi2=[-0.87810809, 0.06485156, 0.47404689]
            chi2=np.array(chi2)*0.8/np.linalg.norm(chi2)
            sk = surrkick(q=q , chi1=chi1,chi2=chi2)
            x0,y0,z0=sk.xoft[sk.times==min(abs(sk.times))][0]
            x,y,z=np.transpose(sk.xoft)
            xnew=spline(sk.times,x)(tnew)
            ynew=spline(sk.times,y)(tnew)
            znew=spline(sk.times,z)(tnew)
            tmax=30 # Fix annyoing bug in matplotlib.axes3d (comes from a 2d backend)
            temp=xnew[tnew<tmax]
            xnew=np.append(temp,temp[-1]*np.ones(len(xnew[tnew>=tmax])))
            temp=ynew[tnew<tmax]
            ynew=np.append(temp,temp[-1]*np.ones(len(ynew[tnew>=tmax])))
            temp=znew[tnew<tmax]
            znew=np.append(temp,temp[-1]*np.ones(len(znew[tnew>=tmax])))

            def _recoil(tilltime):
                fig = plt.figure(figsize=(6,6))
                ax=fig.add_axes([0,0,0.7,0.7], projection='3d')
                fig.text(0.25,0.67,'$t='+str(int(tilltime))+'M$',transform=fig.transFigure, horizontalalignment='left',verticalalignment='bottom')
                x=xnew[tnew<tilltime]
                y=ynew[tnew<tilltime]
                z=znew[tnew<tilltime]
                ax.plot(x-x0,y-y0,z-z0)
                if tilltime>0:
                    ax.scatter(0,0,0,marker='.',s=60,alpha=0.5)
                x,y,z=np.transpose(sk.xoft)
                vx,vy,vz=np.transpose(sk.voft)
                ax.set_xlim(-0.006,0.005)
                ax.set_ylim(-0.006,0.005)
                ax.set_zlim(0,0.011)
                ax.set_xticklabels(ax.get_xticks(), fontsize=9)
                ax.set_yticklabels(ax.get_yticks(), fontsize=9)
                ax.set_zticklabels(ax.get_zticks(), fontsize=9)
                fig.text(0.09,0.45,'$q='+str(q)+'$\n$\\boldsymbol{\\chi_1}=['+
                    str(round(chi1[0],2))+','+str(round(chi1[1],2))+','+str(round(chi1[2],2))+
                    ']$\n$\\boldsymbol{\\chi_2}=['+
                    str(round(chi2[0],2))+','+str(round(chi2[1],2))+','+str(round(chi2[2],2))+
                    ']$\n$v_k='+str(int(convert.kms(sk.kick)))+'{\\rm \;km/s}$',transform=fig.transFigure)
                ax.set_xlabel("$x\;\;[M]$",fontsize=13)
                ax.set_ylabel("$y\;\;[M]$",fontsize=13)
                ax.set_zlabel("$z\;\;[M]$",fontsize=13)
                ax.xaxis.labelpad=6
                ax.yaxis.labelpad=8
                ax.zaxis.labelpad=4
                ax.xaxis.set_minor_locator(AutoMinorLocator())
                ax.yaxis.set_minor_locator(AutoMinorLocator())
                ax.zaxis.set_minor_locator(AutoMinorLocator())
                return fig

            figs=[_recoil(t) for t in tqdm(tnew)]
            allfig.append(figs)

        else:
            allfig.append([None])



        return allfig

    @classmethod
    @plottingstuff
    def hangupErad(self):
        '''Fig. FIX_PAPER_FIG of FIX_PAPER_REF'''

        figs=[]
        L=0.7
        H=0.35
        S=0.05
        fig = plt.figure(figsize=(6,6))
        ax = fig.add_axes([0,0,L,H])

        q=0.5
        chimag=0.8
        sks=[ surrkick(q=q,chi1=[0,0,chimag],chi2=[0,0,chimag]),
            surrkick(q=q,chi1=[0,0,-chimag],chi2=[0,0,-chimag]),
            surrkick(q=q,chi1=[0,0,chimag],chi2=[0,0,-chimag]),
            surrkick(q=q, chi1=[0,0,-chimag],chi2=[0,0,chimag]),
            surrkick(q=q, chi1=[0,0,0],chi2=[0,0,0])]
        labels=["$\chi_i\!=\!0.8$, up-up","$\chi_i\!=\!0.8$, down-down","$\chi_i\!=\!0.8$, up-down","$\chi_i\!=\!0.8$, down-up","$\chi_i=0$"]
        dashes=['',[15,5],[8,5],[2,2],[0.5,1]]
        cols=['C0','C1','C2','C3','black']
        for sk,l,d,c in tqdm(zip(sks,labels,dashes,cols)):
            ax.plot(sk.times,sk.Eoft,alpha=0.4,lw=1,c=c)
            ax.plot(sk.times,sk.Eoft,alpha=1,lw=2,c=c,dashes=d,label=l)

        ax.legend(loc="upper left",fontsize=11,handlelength=5.5)
        ax.text(0.8,0.1,'$q='+str(q)+'$',transform=ax.transAxes,linespacing=1.4)
        ax.set_xlim(-50,50)
        ax.yaxis.set_minor_locator(AutoMinorLocator())
        ax.xaxis.set_minor_locator(AutoMinorLocator())
        ax.set_xlabel("$t\;\;[M]$")
        ax.set_ylim(0,0.08)
        ax.set_ylabel("${E(t)} \;\;[M]$")

        return fig

    @classmethod
    @plottingstuff
    def spinaligned(self):
        '''Fig. FIX_PAPER_FIG of FIX_PAPER_REF'''

        figs=[]
        L=0.7
        H=0.3
        S=0.05

        for q in tqdm([1,0.5]):

            fig = plt.figure(figsize=(6,6))
            axs = [fig.add_axes([0,-i*(S+H),L,H]) for i in [0,1,2,3]]

            chimag=0.8
            sks=[ surrkick(q=q,chi1=[0,0,chimag],chi2=[0,0,chimag],t_ref=-100), surrkick(q=q,chi1=[0,0,-chimag],chi2=[0,0,-chimag],t_ref=-100),
            surrkick(q=q,chi1=[0,0,chimag],chi2=[0,0,-chimag],t_ref=-100),
            surrkick(q=q, chi1=[0,0,-chimag],chi2=[0,0,chimag],t_ref=-100)]
            labels=["up-up","down-down","up-down","down-up"]
            dashes=['',[15,5],[8,5],[2,2]]
            cols=['C0','C1','C2','C3']
            for sk,l,d,c in tqdm(zip(sks,labels,dashes,cols)):
                axs[0].plot(sk.times,1./0.001*project(sk.voft,[1,0,0]),alpha=0.4,lw=1,c=c)
                axs[0].plot(sk.times,1./0.001*project(sk.voft,[1,0,0]),alpha=1,lw=2,c=c,dashes=d)
                axs[1].plot(sk.times,1./0.001*project(sk.voft,[0,1,0]),alpha=0.4,lw=1,c=c)
                axs[1].plot(sk.times,1./0.001*project(sk.voft,[0,1,0]),alpha=1,lw=2,c=c,dashes=d)
                axs[2].plot(sk.times,1./0.001*project(sk.voft,[0,0,1]),alpha=0.4,lw=1,c=c)
                axs[2].plot(sk.times,1./0.001*project(sk.voft,[0,0,1]),alpha=1,lw=2,c=c,dashes=d,label=l)
                axs[3].plot(sk.times,1./0.001*project(sk.voft,sk.kickdir),alpha=0.4,lw=1,c=c)
                axs[3].plot(sk.times,1./0.001*project(sk.voft,sk.kickdir),alpha=1,lw=2,c=c,dashes=d)
            axs[2].legend(loc="lower left",fontsize=14,ncol=2,handlelength=3.86)
            axs[0].text(0.05,0.7,'$q='+str(q)+'$\n$\chi_1=\chi_2=0.8$',transform=axs[    0].transAxes,linespacing=1.4)
            for ax in axs:
                ax.set_xlim(-50,50)
                ax.yaxis.set_major_locator(MultipleLocator(0.5))
                ax.yaxis.set_minor_locator(MultipleLocator(0.25))
                ax.xaxis.set_minor_locator(AutoMinorLocator())
            for ax in axs[:-1]:
                ax.set_xticklabels([])
            for ax in [axs[-1]]:
                ax.set_xlabel("$t\;\;[M]$")
            for ax,d in zip(axs,["x","y","z","v_k"]):
                ax.set_ylim(-1.4,1.4)
                ax.set_ylabel("$\mathbf{v}(t) \cdot \mathbf{\hat "+d+"} \;\;[0.001c]$")

            figs.append(fig)

        return figs

    @classmethod
    @plottingstuff
    def leftright(self):
        '''Fig. FIX_PAPER_FIG of FIX_PAPER_REF'''

        figs=[]
        L=0.7
        H=0.3
        S=0.05
        Z=0.35

        for q in tqdm([1,0.5]):

            fig = plt.figure(figsize=(6,6))
            axs = [fig.add_axes([0,-i*(S+H),L,H]) for i in [0,1,2,3]]
            axi = fig.add_axes([0,-1*(S+H)-S-Z*H,Z*L*1.2,Z*H])

            chimag1=0.8
            chimag2=0.8
            sks=[ surrkick(q=q,chi1=[chimag1,0,0],chi2=[chimag2,0,0],t_ref=-125),
            surrkick(q=q,chi1=[-chimag1,0,0],chi2=[-chimag2,0,0],t_ref=-125),
            surrkick(q=q,chi1=[chimag1,0,0],chi2=[-chimag2,0,0],t_ref=-125),
            surrkick(q=q, chi1=[-chimag1,0,0],chi2=[chimag2,0,0],t_ref=-125)]
            labels=["right-right","left-left","right-left","left-right"]
            dashes=['',[15,5],[8,5],[2,2]]
            cols=['C0','C1','C2','C3']
            for sk,l,d,c in tqdm(zip(sks,labels,dashes,cols)):
                axs[0].plot(sk.times,1./0.001*project(sk.voft,[1,0,0]),alpha=0.4,lw=1,c=c)
                axs[0].plot(sk.times,1./0.001*project(sk.voft,[1,0,0]),alpha=1,lw=2,c=c,dashes=d)
                axs[1].plot(sk.times,1./0.001*project(sk.voft,[0,1,0]),alpha=0.4,lw=1,c=c)
                axs[1].plot(sk.times,1./0.001*project(sk.voft,[0,1,0]),alpha=1,lw=2,c=c,dashes=d,label=l)
                for ax in [axs[2],axi]:
                    ax.plot(sk.times,1./0.001*project(sk.voft,[0,0,1]),alpha=0.4,lw=1,c=c)
                    ax.plot(sk.times,1./0.001*project(sk.voft,[0,0,1]),alpha=1,lw=2,c=c,dashes=d)
                axs[3].plot(sk.times,1./0.001*project(sk.voft,sk.kickdir),alpha=0.4,lw=1,c=c)
                axs[3].plot(sk.times,1./0.001*project(sk.voft,sk.kickdir),alpha=1,lw=2,c=c,dashes=d)

            axs[1].legend(loc="lower left",fontsize=14,ncol=2,handlelength=3.86)
            axs[0].text(0.05,0.7,'$q='+str(q)+'$\n$\chi_1=\chi_2=0.8$',transform=axs[    0].transAxes,linespacing=1.4)
            for ax in axs:
                ax.set_xlim(-50,50)
                ax.yaxis.set_major_locator(MultipleLocator(5))
                ax.yaxis.set_minor_locator(MultipleLocator(1))
                ax.xaxis.set_minor_locator(AutoMinorLocator())
            for ax in axs[:-1]:
                ax.set_xticklabels([])
            for ax in [axs[-1]]:
                ax.set_xlabel("$t\;\;[M]$")
            for ax,d in zip(axs,["x","y","z","v_k"]):
                ax.set_ylim(-10,10)
                ax.set_ylabel("$\mathbf{v}(t) \cdot \mathbf{\hat "+d+"} \;\;[0.001c]$")
            axi.set_xlim(-450,-150)
            axi.set_ylim(-0.02,0.02)
            axi.yaxis.tick_right()
            axi.set_xticks([-400,-300,-200])
            axi.set_yticks([-0.015,0,0.015])
            axi.set_yticklabels(axi.get_yticks(),fontsize=8)
            axi.xaxis.set_tick_params(pad=1)
            axi.set_xticklabels(axi.get_xticks(),fontsize=8)
            axi.yaxis.set_tick_params(pad=1)
            axi.yaxis.set_ticks_position('right')
            axi.xaxis.set_ticks_position('bottom')

            figs.append(fig)

        return figs


    @classmethod
    @plottingstuff
    def alphaseries(self):
        '''Fig. FIX_PAPER_FIG of FIX_PAPER_REF. This is similar to Fig 4 in arXiv:0707.0135.'''

        fig = plt.figure(figsize=(6,6))
        L=0.7
        H=0.3
        S=0.05
        axs = [fig.add_axes([i*(S+L),0,L,H]) for i in [0,1]]

        dim=200
        chimag=0.8
        tref_vals=np.linspace(-250,-100,dim)
        kick_vals=[]
        for t_ref in tqdm(tref_vals):
            sk = surrkick(q=1 , chi1=[chimag,0,0],chi2=[-chimag,0,0],t_ref=t_ref)
            kick_vals.append(sk.kick)

        axs[0].plot(tref_vals,1/0.001*np.array(kick_vals))
        axs[0].scatter(-125,1/0.001*surrkick(q=1 , chi1=[chimag,0,0],chi2=[-chimag,0,0],t_ref=-125).kick,marker='o',edgecolor='C1',facecolor='none',s=100,linewidth='2')
        axs[0].scatter(-100,1/0.001*surrkick(q=1 , chi1=[chimag,0,0],chi2=[-chimag,0,0],t_ref=-100).kick,marker='x',edgecolor='gray',facecolor='gray',s=100,linewidth='2')
        alpha_vals=np.linspace(-np.pi,np.pi,dim)
        kick_vals=[]
        for alpha in tqdm(alpha_vals):
            sk = surrkick(q=1 , chi1=[chimag*np.cos(alpha),chimag*np.sin(alpha),0],chi2=[-chimag*np.cos(alpha),-chimag*np.sin(alpha),0])
            kick_vals.append(sk.kick)
        axs[1].plot(alpha_vals,1/0.001*np.array(kick_vals),c='C3')
        axs[1].scatter(0,1/0.001*surrkick(q=1 , chi1=[chimag,0,0],chi2=[-chimag,0,0],t_ref=-100).kick,marker='x',edgecolor='gray',facecolor='gray',s=100,linewidth='2')

        axs[0].text(0.05,0.5,'$q=1$\n$\chi_1=\chi_2=0.8$\n$\\alpha=0$',transform=axs[0].transAxes,linespacing=1.4)
        axs[1].text(0.05,0.5,'$q=1$\n$\chi_1=\chi_2=0.8$\n$t_{\\rm ref}=-100M$',transform=axs[1].transAxes,linespacing=1.4)
        axs[1].set_yticklabels([])
        axs[0].set_xlabel("$t_{\\rm ref}\;\;[M]$")
        axs[1].set_xlim(-1.1*np.pi,1.1*np.pi)
        axs[1].set_xlabel("$\\alpha$")
        axs[1].set_xticks([-np.pi,-np.pi/2,0,np.pi/2,np.pi])
        axs[1].set_xticklabels(['$-\pi$','$-\pi/2$','$0$','$\pi/2$','$\pi$'])
        axs[0].set_ylabel("${v_k} \;\;[0.001 c]$")
        for ax in axs:
            ax.set_ylim(0,10)
            ax.xaxis.set_minor_locator(AutoMinorLocator())
            ax.yaxis.set_minor_locator(AutoMinorLocator())

        return fig

    @classmethod
    @plottingstuff
    def alphaprof(self):
        '''Fig. FIX_PAPER_FIG of FIX_PAPER_REF.'''

        fig = plt.figure(figsize=(6,6))
        ax=fig.add_axes([0,0,0.7,0.3])
        chimag=0.5

        ax.axvline(0,c='black',alpha=0.3,ls='dotted')
        ax.axhline(0,c='black',alpha=0.3,ls='dotted')
        alpha_vals=np.linspace(-np.pi,np.pi,50)
        kick_vals=[]
        for i, alpha in enumerate(tqdm(alpha_vals)):
            sk = surrkick(q=1, chi1=[chimag*np.cos(alpha),chimag*np.sin(alpha),0],chi2=[-chimag*np.cos(alpha),-chimag*np.sin(alpha),0])
            color=plt.cm.copper(i/len(alpha_vals))
            ax.plot(sk.times,1/0.001*project(sk.voft,sk.kickdir),color=color,alpha=0.8)
        ax.set_xlim(-100,50)
        ax.set_xlabel("$t\;\;[M]$")
        ax.set_ylabel("$\mathbf{v}(t)\cdot \mathbf{\hat v_k} \;\;[0.001 c]$")
        ax.xaxis.set_minor_locator(AutoMinorLocator())
        ax.yaxis.set_minor_locator(AutoMinorLocator())
        ax.text(0.05,0.55,'$q=1$\n$\chi_1=\chi_2=0.8$\n$\\alpha=-\pi ... \pi$',transform=ax.transAxes,linespacing=1.4)

        return fig

    @classmethod
    @plottingstuff
    def lineofsight(self):
        '''Fig. FIX_PAPER_FIG of FIX_PAPER_REF'''

        L=0.7
        H=0.6
        S=0.05
        fig = plt.figure(figsize=(6,6))
        ax = fig.add_axes([0,0,L,H])
        ax.axhline(0,c='black',alpha=0.3,ls='dotted')

        q=0.5
        chimag=0.8
        sk= surrkick(q=q,chi1=[0,0,chimag],chi2=[0,0,-chimag],t_ref=-100)
        dim=15
        store=[]
        for i in tqdm(np.linspace(0,1,dim)):
            phi = np.random.uniform(0,2*np.pi)
            theta = np.arccos(np.random.uniform(-1,1))
            randomvec= [np.sin(theta)*np.cos(phi), np.sin(theta)*np.sin(phi), np.cos(theta)]
            store.append([randomvec,project(sk.voft,randomvec)[-1]])

        store=sorted(store, key=lambda x:x[1])

        for i,rv in tqdm(zip(np.linspace(0,1,dim),[x[0] for x in store])):
            color=plt.cm.copper(i)
            ax.plot(sk.times,1./0.001*project(sk.voft,rv),c=color,alpha=0.8)

        ax.text(0.05,0.75,'$q='+str(q)+'$\n$\chi_1=\chi_2=0.8$\n right-left',transform=ax.transAxes,linespacing=1.4)
        ax.set_xlim(-50,50)
        ax.yaxis.set_major_locator(MultipleLocator(0.5))
        ax.yaxis.set_minor_locator(MultipleLocator(0.1))
        ax.xaxis.set_minor_locator(AutoMinorLocator())
        ax.set_xlabel("$t\;\;[M]$")
        ax.set_ylabel("$\mathbf{v}(t) \cdot \mathbf{\hat n} \;\;[0.001c]$")

        return fig


    @classmethod
    def findlarge(self):
        '''Fig. FIX_PAPER_FIG of FIX_PAPER_REF'''

        dim=int(1e5)
        filename='findlarge.pkl'
        if not os.path.isfile(filename):

            def _kickdistr(i):
                np.random.seed()
                #q=np.random.uniform(0.5,1)
                q=1
                phi = np.random.uniform(0,2*np.pi)
                theta = np.arccos(np.random.uniform(-1,1))
                #r = 0.8*(np.random.uniform(0,1))**(1./3.)
                r=0.8
                chi1= [ r*np.sin(theta)*np.cos(phi), r*np.sin(theta)*np.sin(phi), r*np.cos(theta) ]
                phi = np.random.uniform(0,2*np.pi)
                theta = np.arccos(np.random.uniform(-1,1))
                #r = 0.8*(np.random.uniform(0,1))**(1./3.)
                r=0.8
                chi2= [ r*np.sin(theta)*np.cos(phi), r*np.sin(theta)*np.sin(phi), r*np.cos(theta) ]
                sk= surrkick(q=q,chi1=chi1,chi2=chi2)
                return [q,chi1,chi2,sk.kick]

            print("Running in parallel on", multiprocessing.cpu_count(),"cores. Storing data:", filename)
            parmap = pathos.multiprocessing.ProcessingPool(multiprocessing.cpu_count()).imap
            data= list(tqdm(parmap(_kickdistr, range(dim)),total=dim))

            with open(filename, 'wb') as f: pickle.dump(zip(*data), f)
        with open(filename, 'rb') as f: q,chi1,chi2,kicks = pickle.load(f)

        mk=max(kicks)
        print(mk)
        maxind= kicks==mk
        print("Largest kick:", mk, convert.kms(mk))
        chi1m= np.array(chi1)[maxind][0]/0.8
        chi2m= np.array(chi2)[maxind][0]/0.8
        print("chi1=", chi1m, 'theta1=',np.degrees(np.arccos(chi1m[-1])))
        print("chi2=", chi2m, 'theta2=',np.degrees(np.arccos(chi2m[-1])))
        return []

    @classmethod
    @plottingstuff
    def normprofiles(self):
        '''Fig. FIX_PAPER_FIG of FIX_PAPER_REF'''

        levels = np.linspace(0,1.6,100)
        CS3 = plt.contourf([[0,0],[0,0]], levels, cmap=plt.cm.copper,extend='max')
        plt.clf()

        fig = plt.figure(figsize=(4,4))
        ax=fig.add_axes([0,0,0.7,0.7])
        ax.axhline(0,c='black',alpha=0.3,ls='dotted')
        ax.axhline(1,c='black',alpha=0.3,ls='dotted')

        filename='normprofiles.pkl'
        if not os.path.isfile(filename):
            print("Storing data:", filename)

            data=[]
            for i in tqdm(range(200)):
                q=np.random.uniform(0.5,1)
                phi = np.random.uniform(0,2*np.pi)
                theta = np.arccos(np.random.uniform(-1,1))
                r = 0.8*(np.random.uniform(0,1))**(1./3.)
                chi1= [ r*np.sin(theta)*np.cos(phi), r*np.sin(theta)*np.sin(phi), r*np.cos(theta) ]
                phi = np.random.uniform(0,2*np.pi)
                theta = np.arccos(np.random.uniform(-1,1))
                r = 0.8*(np.random.uniform(0,1))**(1./3.)
                chi2= [ r*np.sin(theta)*np.cos(phi), r*np.sin(theta)*np.sin(phi), r*np.cos(theta) ]
                sk= surrkick(q=q,chi1=chi1,chi2=chi2)
                phi = np.random.uniform(0,2*np.pi)
                theta = np.arccos(np.random.uniform(-1,1))
                randomdir= [np.sin(theta)*np.cos(phi), np.sin(theta)*np.sin(phi), np.cos(theta) ]
                data.append([sk.kick, project(sk.voft/sk.kick,sk.kickdir)])

            with open(filename, 'wb') as f: pickle.dump(data, f)
        with open(filename, 'rb') as f: data = pickle.load(f)

        times=surrkick().times
        for d in tqdm(data):
            ax.plot(times,d[1],alpha=0.7, c= plt.cm.copper(d[0]/0.0016),lw=1)

        axcb = fig.add_axes([0.72,0,0.05,0.7])
        cb = fig.colorbar(CS3,cax=axcb,boundaries=np.linspace(0,1.6,100),ticks=np.linspace(0,1.6,9))
        ax.plot(times,scipy.stats.norm.cdf(times, loc=10, scale=8),dashes=[10,4],c='C0',lw=2)
        ax.plot(times,scipy.stats.norm.cdf(times, loc=10, scale=8),c='C0',alpha=0.5,lw=1)
        ax.set_xlim(-50,50)
        ax.set_ylim(-2.5,2.5)
        ax.xaxis.set_major_locator(MultipleLocator(20))
        ax.xaxis.set_minor_locator(AutoMinorLocator())
        ax.yaxis.set_minor_locator(AutoMinorLocator())
        cb.set_label('$v_k\;\;[0.001c]$')
        ax.set_xlabel("$t\;\;[M]$")
        ax.set_ylabel("$\mathbf{v}(t)\cdot\mathbf{\hat n}\, /\, \mathbf{v_k}\cdot\mathbf{\hat n}$")

        return fig

    @classmethod
    @plottingstuff
    def explore(self):
        '''Fig. FIX_PAPER_FIG of FIX_PAPER_REF'''

        fig = plt.figure(figsize=(6,6))
        L=0.6
        H=0.7
        S=0.12
        s=0.04
        Li=0.35
        Hi=0.25
        axv= fig.add_axes([0,0,H,H])
        axE= fig.add_axes([H+S-0.02,(H-S)/2+S,L,(H-S)/2])
        axJ= fig.add_axes([H+S-0.02,0,L,(H-S)/2])
        axi = fig.add_axes([H-Li-s,H-Hi-0.15,Li,Hi])

        dim=int(1e6)

        filename='explore.pkl'
        if not os.path.isfile(filename):

            def _explore(i):
                print(' ',i)
                np.random.seed()
                q=np.random.uniform(0.5,1)
                phi = np.random.uniform(0,2*np.pi)
                theta = np.arccos(np.random.uniform(-1,1))
                r = 0.8*(np.random.uniform(0,1))**(1./3.)
                chi1= [ r*np.sin(theta)*np.cos(phi), r*np.sin(theta)*np.sin(phi), r*np.cos(theta) ]
                phi = np.random.uniform(0,2*np.pi)
                theta = np.arccos(np.random.uniform(-1,1))
                r = 0.8*(np.random.uniform(0,1))**(1./3.)
                chi2= [ r*np.sin(theta)*np.cos(phi), r*np.sin(theta)*np.sin(phi), r*np.cos(theta) ]
                sk= surrkick(q=q,chi1=chi1,chi2=chi2)
                q=np.random.uniform(0.5,1)
                chi1m = 0.8*(np.random.uniform(0,1))**(1./3.)
                chi2m = 0.8*(np.random.uniform(0,1))**(1./3.)
                theta1=np.arccos(np.random.uniform(-1,1))
                theta2=np.arccos(np.random.uniform(-1,1))
                deltaphi = np.random.uniform(0,2*np.pi)
                dummy,dummy,dummy,S1,S2=precession.get_fixed(q,chi1m,chi2m)
                fk=precession.finalkick(theta1,theta2,deltaphi,q,S1,S2,maxkick=False,kms=False,more=False)
                return [sk.Erad,sk.kick,sk.Jrad,fk]

            print("Running in parallel on", multiprocessing.cpu_count(),"cores. Storing data:", filename)
            parmap = pathos.multiprocessing.ProcessingPool(multiprocessing.cpu_count()).imap
            #data= list(tqdm(parmap(_explore, range(dim)),total=dim))
            data= map(_explore, range(dim))

            with open(filename, 'wb') as f: pickle.dump(zip(*data), f)
        with open(filename, 'rb') as f: Erad,kicks,Jrad,fk = pickle.load(f)
        kicks=np.array(kicks)
        fk=np.array(fk)

        nbins=100
        axE.hist(Erad,bins=nbins,weights=np.ones_like(Erad)/dim,histtype='step',lw=2,alpha=0.8,color='C0',normed=True)
        axE.hist(Erad,bins=nbins,weights=np.ones_like(Erad)/dim,histtype='stepfilled',alpha=0.2,color='C0',normed=True)
        for ax in [axv,axi]:
            ax.hist(1/0.001*fk,bins=nbins,weights=np.ones_like(Erad)/dim,histtype='step',lw=2,alpha=0.8,color='C3',label='Fitting formula',normed=True)
            ax.hist(1/0.001*fk,bins=nbins,weights=np.ones_like(Erad)/dim,histtype='stepfilled',alpha=0.2,color='C3',normed=True)
            ax.hist(1/0.001*kicks,bins=nbins,weights=np.ones_like(Erad)/dim,histtype='step',lw=2,alpha=0.8,color='C0',label='Surrogate',normed=True)
            ax.hist(1/0.001*kicks,bins=nbins,weights=np.ones_like(Erad)/dim,histtype='stepfilled',alpha=0.2,color='C0',normed=True)
        axJ.hist(Jrad,bins=nbins,weights=np.ones_like(Erad)/dim,histtype='step',lw=2,alpha=0.8,color='C0',normed=True)
        axJ.hist(Jrad,bins=nbins,weights=np.ones_like(Erad)/dim,histtype='stepfilled',alpha=0.2,color='C0',normed=True)

        axE.set_xlabel("$E \;\;[M]$")
        axv.set_xlabel("$v_k\;\;[0.001c]$")
        axJ.set_xlabel("$J\;\;[M^2]$")
        for ax in [axE,axv,axJ]:
            ax.xaxis.set_minor_locator(AutoMinorLocator())
            ax.yaxis.set_minor_locator(AutoMinorLocator())
        axv.xaxis.set_minor_locator(MultipleLocator(0.5))
        axv.xaxis.set_major_locator(MultipleLocator(2))
        axv.legend(loc='upper right',fontsize=15)
        axE.set_xlim(0.015,0.085)
        axE.set_ylim(0,60)
        axv.set_xlim(-0.5,12.5)
        axv.set_ylim(0,0.35)
        axJ.set_ylim(0,8)
        axi.set_xlim(8,11)
        axi.set_ylim(0,0.01)
        axJ.yaxis.set_major_locator(MultipleLocator(2))
        axi.xaxis.set_major_locator(MultipleLocator(1))
        axi.yaxis.set_major_locator(MultipleLocator(0.002))
        axi.set_yticklabels(axi.get_yticks(),fontsize=12)
        axi.set_xticklabels(axi.get_xticks(),fontsize=12)
        from mpl_toolkits.axes_grid1.inset_locator import mark_inset
        mark_inset(axv, axi, loc1=4, loc2=3, fc="none", ec="0.5",alpha=0.8)

        print("Largest kicks.\n\tSurrogate:", convert.kms(max(kicks)), "\n\tFitting formula", convert.kms(max(fk)))
        print("P(vk>2000 km/s).\n\tSurrogate:", len(kicks[kicks>convert.cisone(2000)])/dim, "\n\tFitting formula", len(fk[fk>convert.cisone(2000)])/dim)

        return fig

    @classmethod
    def timing(self):
        '''Fig. FIX_PAPER_FIG of FIX_PAPER_REF'''

        dim=1000
        surrogate().sur() # Load the surrogate once for all

        timessur=[]
        timeskick=[]
        timesall=[]
        for i in tqdm(range(dim)):

            q=np.random.uniform(0.5,1)
            phi = np.random.uniform(0,2*np.pi)
            theta = np.arccos(np.random.uniform(-1,1))
            r = 0.8*(np.random.uniform(0,1))**(1./3.)
            chi1= [ r*np.sin(theta)*np.cos(phi), r*np.sin(theta)*np.sin(phi), r*np.cos(theta) ]
            phi = np.random.uniform(0,2*np.pi)
            theta = np.arccos(np.random.uniform(-1,1))
            r = 0.8*(np.random.uniform(0,1))**(1./3.)
            chi2= [ r*np.sin(theta)*np.cos(phi), r*np.sin(theta)*np.sin(phi), r*np.cos(theta) ]
            sk= surrkick(q=q,chi1=chi1,chi2=chi2)

            t0=time.time()
            sk.hsample
            tsur=time.time()-t0

            t0=time.time()
            sk.kick
            tkick=time.time()-t0

            t0=time.time()
            sk=surrkick(q=q,chi1=chi1,chi2=chi2).kick
            tall=time.time()-t0

            timessur.append(tsur)
            timeskick.append(tkick)
            timesall.append(tall)

        print("Time, surrogate waveform:", np.mean(timessur),'s')
        print("Time, kick:", np.mean(timeskick),'s')
        print("Time, both:", np.mean(timesall),'s')


    @classmethod
    @plottingstuff
    def symmetry(self):
        L=0.7
        H=0.4
        S=0.15

        fig = plt.figure(figsize=(6,6))
        ax = [fig.add_axes([0,-i*(S+H),L,H]) for i in [0,1,2]]

        dim=int(1e4)
        filename='symmetry.pkl'
        if not os.path.isfile(filename):

            kicks=[[],[],[]]

            for i in tqdm(range(dim)):
                q=1
                phi = np.random.uniform(0,2*np.pi)
                theta = np.arccos(np.random.uniform(-1,1))
                r = 0.8*(np.random.uniform(0,1))**(1./3.)
                chi1 = [ r*np.sin(theta)*np.cos(phi), r*np.sin(theta)*np.sin(phi), r*np.cos(theta) ]
                chi2 = chi1
                kicks[0].append(surrkick(q=q,chi1=chi1,chi2=chi2).kick)

            for i in tqdm(range(dim)):
                q=np.random.uniform(0.5,1)
                chi1 = [ 0, 0, np.random.uniform(-0.8,0.8) ]
                chi2 = [ 0, 0, np.random.uniform(-0.8,0.8) ]
                kicks[1].append(surrkick(q=q,chi1=chi1,chi2=chi2).kickcomp[2])

            for i in tqdm(range(dim)):
                q=1
                phi = np.random.uniform(0,2*np.pi)
                theta = np.arccos(np.random.uniform(-1,1))
                r = 0.8*(np.random.uniform(0,1))**(1./3.)
                chi1 = [ r*np.sin(theta)*np.cos(phi), r*np.sin(theta)*np.sin(phi), r*np.cos(theta) ]
                chi2 = [-chi1[0],-chi1[1],chi1[2]]
                kicks[2].append(np.linalg.norm(np.cross(surrkick(q=q,chi1=chi1,chi2=chi2).kickcomp,[0,0,1])))

            print("Storing datxa:", filename)

            with open(filename, 'wb') as f: pickle.dump(kicks, f)
        with open(filename, 'rb') as f: kicks = pickle.load(f)

        nbins=100
        for axx,kick in zip(ax,kicks):
            axx.hist(1/0.001*np.abs(kick),bins=nbins,histtype='step',lw=2,alpha=1,color='C4',normed=True)
            axx.hist(1/0.001*np.abs(kick),bins=nbins,histtype='stepfilled',alpha=0.3,color='C4',normed=True)
            print(np.percentile(1/0.001*np.abs(kick), 50),np.percentile(1/0.001*np.abs(kick), 90))
            axx.axvline(np.percentile(1/0.001*np.abs(kick), 50),c='gray',ls='dashed')
            axx.axvline(np.percentile(1/0.001*np.abs(kick), 90),c='gray',ls='dotted')

        ax[0].set_xlabel("$v_k \;\;[0.001c]$")
        ax[1].set_xlabel("$|\mathbf{v_k}\cdot \mathbf{\hat z}| \;\;[0.001c]$")
        ax[2].set_xlabel("$|\mathbf{v_k}\\times \mathbf{\hat z}| \;\;[0.001c]$")
        ax[0].text(0.5,0.9,'$q=1$\n$\\boldsymbol{\\chi_1}=\\boldsymbol{\\chi_2}$\n$\longrightarrow v_k=0$',verticalalignment='top', transform=ax[0].transAxes,linespacing=1.6)
        ax[1].text(0.5,0.9,'${\\rm Generic}\; q$\n$\\boldsymbol{\\chi_1}\\times \mathbf{\hat z} = \\boldsymbol{\\chi_2}\\times \mathbf{\hat z}=0$\n$\longrightarrow \mathbf{v_k}\cdot\mathbf{\hat z}=0$',verticalalignment='top',transform=ax[1].transAxes,linespacing=1.6)
        ax[2].text(0.5,0.9,'$q=1$\n$\\boldsymbol{\\chi_1}\\cdot \mathbf{\hat z}=\\boldsymbol{\\chi_2}\\cdot \mathbf{\hat z}$\n$\\boldsymbol{\\chi_1}\\times \mathbf{\hat z} = -\\boldsymbol{\\chi_2}\\times \mathbf{\hat z}$\n$\longrightarrow \mathbf{v_k}\\times\mathbf{\hat z}=0$',verticalalignment='top',transform=ax[2].transAxes,linespacing=1.6)
        for axx in ax:
            axx.xaxis.set_minor_locator(AutoMinorLocator())
            axx.yaxis.set_minor_locator(AutoMinorLocator())

        return fig


    @classmethod
    @plottingstuff
    def nr_comparison_histograms(self):

        fig = plt.figure(figsize=(6,6))
        ax = fig.add_axes([0,0,0.7,0.5])

        nr100 = np.loadtxt("../nr_comparison_data/nr_kicks_t100.dat")
        nr4500 = np.loadtxt("../nr_comparison_data/nr_kicks_t4500.dat")

        def _nr_surr_comparison_data_helper(nr_data, t):
            kicks = []
            for d in nr_data:
                q = d[2]
                chi1 = [d[3], d[4], d[5]]
                chi2 = [d[6], d[7], d[8]]
                kicks.append(surrkick(q=q, chi1=chi1, chi2=chi2, t_ref=t).kick)
            return np.array(kicks)

        filename='nr_comparison_kicks_t100.pkl'
        if not os.path.isfile(filename):
            surr_kicks = _nr_surr_comparison_data_helper(nr100, -100)
            print("Storing data:", filename)
            with open(filename, 'wb') as f: pickle.dump(surr_kicks, f)
        with open(filename, 'rb') as f: surr100 = pickle.load(f)

        filename='nr_comparison_kicks_t4500.pkl'
        if not os.path.isfile(filename):
            surr_kicks = _nr_surr_comparison_data_helper(nr4500, -4500)
            print("Storing data:", filename)
            with open(filename, 'wb') as f: pickle.dump(surr_kicks, f)
        with open(filename, 'rb') as f: surr4500 = pickle.load(f)

        mag_nr = nr4500[:,12] / 0.001
        mag_nr_lev2 = nr4500[:,16] / 0.001
        mag_nr_lmax4 = nr4500[:,20] / 0.001
        mag_surr = surr4500[:] / 0.001
        mag_surr_t100 = surr100[:] / 0.001
        delta_nr_surr = np.fabs(mag_nr - mag_surr)
        delta_nr_levs = np.fabs(mag_nr - mag_nr_lev2)
        delta_nr_lmax = np.fabs(mag_nr - mag_nr_lmax4)
        delta_surr_times = np.fabs(mag_surr - mag_surr_t100)

        logbins = np.logspace(-6, 1.3, 65)
        ax.hist(mag_nr, bins=logbins, histtype='stepfilled', alpha=0.6, label="NR")
        ax.hist(mag_surr, bins=logbins, histtype='stepfilled', alpha=0.6, label="surr")
        ax.hist(delta_nr_surr, bins=logbins, histtype='step', label="NR vs. surr")
        ax.hist(delta_nr_levs, bins=logbins, histtype='step', label="NR lev 3 vs. 2")
        ax.hist(delta_nr_lmax, bins=logbins, histtype='step', label="NR $l_{\mathrm{max}}$ 8 vs. 4")
        ax.hist(delta_surr_times, bins=logbins, histtype='step', label="surr $t$ $-100$ vs. $-4500$")

        # flip some curves for visual clarity (?)
        #ax.plot([0,20], [0,0], color='k')
        #n, b, patches = ax.hist(delta_nr_levs, bins=logbins, histtype='step', label="NR lev 3 vs. 2")
        #for p in patches:
        #    xy = p.get_xy()
        #    xy[:,1] = -xy[:,1]
        #    p.set_xy(xy)
        #n, b, patches = ax.hist(delta_nr_lmax, bins=logbins, histtype='step', label="NR $l_{\mathrm{max}}$ 8 vs. 4")
        #for p in patches:
        #    xy = p.get_xy()
        #    xy[:,1] = -xy[:,1]
        #    p.set_xy(xy)
        #ax.set_ylim(-100,150)

        ax.legend(loc=2, ncol=2, fontsize=12)
        ax.set_xscale("log")
        ax.set_xlabel("$v_k\;\;[0.001c]$")
        ax.set_ylim(0,125)
        ax.yaxis.set_minor_locator(MultipleLocator(5))
        ax.xaxis.set_major_locator(LogLocator(numticks=8))
        ax.xaxis.set_minor_locator(LogLocator(numticks=8,subs=(0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,)))
        ax.xaxis.set_minor_formatter(NullFormatter())

        return fig


    @classmethod
    @plottingstuff
    def nr_comparison_scatter(self):

        main_w = 0.6
        main_h = 0.6
        hist_h = 0.15
        gap = 0.05

        fig = plt.figure(figsize=(6, 6))
        ax = fig.add_axes([0, 0, main_w, main_h])
        axt = fig.add_axes([0, main_h + gap, main_w, hist_h])
        axr = fig.add_axes([main_w + gap, 0, hist_h, main_h])

        nr4500 = np.loadtxt("../nr_comparison_data/nr_kicks_t4500.dat")

        # duplicated from histogram plot
        def _nr_surr_comparison_data_helper(nr_data, t):
            kicks = []
            for d in nr_data:
                q = d[2]
                chi1 = [d[3], d[4], d[5]]
                chi2 = [d[6], d[7], d[8]]
                kicks.append(surrkick(q=q, chi1=chi1, chi2=chi2, t_ref=t).kick)
            return np.array(kicks)

        # duplicated from histogram plot
        filename='nr_comparison_kicks_t4500.pkl'
        if not os.path.isfile(filename):
            surr_kicks = _nr_surr_comparison_data_helper(nr4500, -4500)
            print("Storing data:", filename)
            with open(filename, 'wb') as f: pickle.dump(surr_kicks, f)
        with open(filename, 'rb') as f: surr4500 = pickle.load(f)

        mag_nr = nr4500[:,12] / 0.001
        mag_surr = surr4500[:] / 0.001
        diff = np.fabs(mag_nr - mag_surr)
        perc50 = np.percentile(diff, 50)
        perc90 = np.percentile(diff, 90)
        print("  50th percentile of kick magnitude diffs [0.001c]:", perc50)
        print("  90th percentile of kick magnitude diffs [0.001c]:", perc90)
        x = np.array([0,1e4])
        y = np.array([0,1e4])
        y_p50 = y + perc50
        y_m50 = y - perc50
        y_p90 = y + perc90
        y_m90 = y - perc90

        ax.plot(x, y_p50, lw=0.5, ls='dashed', c='gray')
        ax.plot(x, y_m50, lw=0.5, ls='dashed', c='gray')
        ax.plot(x, y_p90, lw=0.5, ls='dotted', c='gray')
        ax.plot(x, y_m90, lw=0.5, ls='dotted', c='gray')
        ax.scatter(mag_nr, mag_surr, s=10, alpha=0.5, edgecolors='none')

        ax.set_xlim(0,10)
        ax.set_ylim(0,10)
        ax.set_xlabel("NR kick $[0.001c]$")
        ax.set_ylabel("Surrogate kick $[0.001c]$")
        ax.xaxis.set_major_locator(MultipleLocator(2))
        ax.xaxis.set_minor_locator(MultipleLocator(0.5))
        ax.yaxis.set_major_locator(MultipleLocator(2))
        ax.yaxis.set_minor_locator(MultipleLocator(0.5))

        bins = np.linspace(0, 10, 41)
        axt.hist(mag_nr, bins=bins, histtype='stepfilled')
        axt.set_xlim(0,10)
        axt.set_ylim(0,80)
        axt.axes.xaxis.set_ticklabels([])
        axt.xaxis.set_major_locator(MultipleLocator(2))
        axt.xaxis.set_minor_locator(MultipleLocator(0.5))
        axt.yaxis.set_major_locator(MultipleLocator(40))
        axt.yaxis.set_minor_locator(MultipleLocator(10))

        axr.hist(mag_surr, bins=bins, histtype='stepfilled', orientation='horizontal')
        axr.set_xlim(0,80)
        axr.set_ylim(0,10)
        axr.axes.yaxis.set_ticklabels([])
        axr.xaxis.set_major_locator(MultipleLocator(40))
        axr.xaxis.set_minor_locator(MultipleLocator(10))
        axr.yaxis.set_major_locator(MultipleLocator(2))
        axr.yaxis.set_minor_locator(MultipleLocator(0.5))

        return fig


########################################
if __name__ == "__main__":
    pass
    #plots.centerofmass()
    #plots.symmetry()
    #plots.findlarge()
    #plots.explore()
    #plots.spinaligned()
    #plots.recoil()

    #print(surrkick().kick)
    plots.alphaseries()
    #plots.nospinprofiles()
    #plots.leftright()

    #plots.normprofiles()

    #plots.hangupErad()
    #t=surrkick().times
    #print(t[1:]-t[:-1])
    #plots.recoil()
    #plots.check()
    #plots.nospinprofiles()
    #plots.findlarge()
    #plots.timing()
    #plots.recoil()
    #plots.explore()
    #plots.normprofiles()
    #plots.centerofmass()

    #plots.nr_comparison_histograms()
    #plots.nr_comparison_scatter()
