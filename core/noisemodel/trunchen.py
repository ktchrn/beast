"""
Trunchen version of noisemodel
Goal is to compute the full 6-band covariance matrix for each model

.. history::
    Started 18 Dec 2015 by Karl D. Gordon
"""
from __future__ import print_function

import math
import numpy as np

from scipy.spatial import cKDTree

from .noisemodel_trunchen import NoiseModel
from ..vega import Vega

from ...tools.pbar import Pbar

class MultiFilterASTs(NoiseModel):
    """ Implement a noise model where the ASTs are provided as a single table

    Attributes
    ----------
    astfile: str
        file containing the ASTs

    filters: sequence(str)
        sequence of filter names
    """
    def __init__(self, astfile, filters, *args, **kwargs):
        NoiseModel.__init__(self, astfile, *args, **kwargs)
        self.setFilters(filters)

        # needs updating
        self._input_fluxes = None
        self._biases = None
        self._cov_matrices = None
        self._corr_matrices = None

    def setFilters(self, filters):
        """ set the filters and update the vega reference for the conversions

        Parameters
        ----------
        filters: sequence
            list of filters using the internally normalized namings
        """
        self.filters = filters

        # ASTs inputs are in vega mag whereas models are in flux units
        #     for optimization purpose: pre-compute
        with Vega() as v:
            _, vega_flux, _ = v.getFlux(filters)

        self.vega_flux = vega_flux

    def _calc_ast_cov(self, indxs, filters, return_all=False):
        """
        The NxN-dimensional covariance matrix and N-dimensional bias vector are
        calculated from M independent ASTs computed for N bands

        Parameters
        ----------
        indxs : index array giving the ASTs assocaited with a single
                model SED
        filters : base filter names in the AST file

        Keywords
        --------
        return_all : True/False
        
        Returns
        -------
        if return_all = False
           (cov_mat, bias)
        else
           (cov_mat, bias, stddevs, corr_mat, diffs, imags)

        cov_mat : NxN dim numpy array
                  covariance matrix in flux units
        bias : N dim numpy vector
               vector of the biases in each filter
        stddevs : N dim numpy vector
                  vector of standard deviations in each filter
        corr_mat : NxN dim numpy array
                   correlation matrix
        diffs : KxN dim numpy vector
                raw flux differences for N filters and K AST instances
        imags : N dim numpy vector
                input magnitude of the AST in each filter
        """

        # set the asts for this star using the input index array
        asts = self.data[indxs]

        # now check that the source was recovered in at least 1 band
        #   this replicates how the observed catalog is created
        n_asts = len(asts)
        gtindxs = np.full((n_asts),1)
        for k in range(n_asts):
            cgood = 0
            for cfilter in filters:
                if asts[cfilter+'_VEGA'][k] < 90:
                    cgood = cgood + 1
            gtindxs[k] = cgood

        indxs, = np.where(gtindxs > 0)
        n_indxs = len(indxs)
        if n_indxs <= 5:
            return False

        # setup the variables for output
        n_filters = len(filters)
        imags = np.empty((n_filters),dtype=np.float32)
        diffs = np.empty((n_filters,n_indxs),dtype=np.float32)
        biases = np.empty((n_filters),dtype=np.float32)
        stddevs = np.empty((n_filters),dtype=np.float32)
        cov_matrix = np.full((n_filters, n_filters),0.0,dtype=np.float32)
    
        for ck, cfilter in enumerate(filters):
            imags[ck] = asts[cfilter+'_IN'][indxs[0]]
            # compute the difference vector between the input and output fluxes
            #    note that the input fluxes are in magnitudes and the
            #    output fluxes in normalized vega fluxes
            diffs[ck,:] = asts[cfilter+'_RATE'][indxs] - \
                          np.power(10.0,-0.4*asts[cfilter+'_IN'][indxs])
            # compute the bias and standard deviations around said bias
            biases[ck] = np.mean(diffs[ck,:])
            stddevs[ck] = np.std(diffs[ck,:])

        # compute the covariance matrix
        for ck, cfilter in enumerate(filters):
            for dk, dfilter in enumerate(filters):
                for ci in range(n_indxs):
                    cov_matrix[ck,dk] += (diffs[ck,ci] - biases[ck])* \
                                         (diffs[dk,ci] - biases[dk])
        cov_matrix /= (n_indxs-1)

        # compute the corrleation matrix
        corr_matrix = np.array(cov_matrix)
        for ck, cfilter in enumerate(filters):
            for dk, dfilter in enumerate(filters):
                if stddevs[ck]*stddevs[dk] > 0:
                    corr_matrix[ck,dk] /= stddevs[ck]*stddevs[dk]
                else:
                    corr_matrix[ck,dk] = 0.0

        if return_all:
            return (cov_matrix, biases, stddevs, corr_matrix, diffs, imags)
        else:
            return (cov_matrix, biases)


    def _calc_all_ast_cov(self, filters, progress=True):
        """
        The covariance matrices and biases are calculated for all the
        independent models in the AST file

        Parameters
        ----------
        filters : filter names for the AST data
        
        Keywords
        --------
        progress: bool, optional
            if set, display a progress bar

        Returns
        -------
        (cov_mats, biases, corr_mats, ifluxes)

        cov_mats : KxNxN dim numpy array
                   K AST covariance matrices in flux units
        bias : KxN dim numpy vector
               K vectors of the biases in each filter
        corr_mats : KxNxN dim numpy array
                    K AST correlation matrices
        ifluxes : KxN dim numpy vector
                  K vectors of the input fluxes in each filter
        """

        # find the stars by using unique values of the magnitude values
        #   in filtername
        filtername = filters[-1] + '_IN'
        uvals, ucounts = np.unique(self.data[filtername], return_counts=True)
        n_models = len(uvals)

        # setup the output
        n_filters = len(filters)
        all_covs = np.empty((n_models,n_filters,n_filters),dtype=np.float64)
        all_corrs = np.empty((n_models,n_filters,n_filters),dtype=np.float32)
        all_biases = np.empty((n_models,n_filters),dtype=np.float64)
        all_imags = np.empty((n_models,n_filters),dtype=np.float32)

        # loop over the unique set of models and
        # calculate the covariance matrix using the ASTs for this model
        good_asts = np.full((n_models),True)
        if progress is True:
            it = Pbar(desc='Calculating AST Covariance ' + \
                      'Matrices').iterover(range(n_models))
        else:
            it = range(n_models)
        for i in it:
            # find all the ASTs for this model
            indxs, = np.where(self.data[filtername] == uvals[i])
            n_asts = len(indxs)

            if n_asts > 5:
                results = self._calc_ast_cov(indxs, filters,
                                             return_all=True)
                if results:
                    all_covs[i,:,:] = results[0]
                    all_biases[i,:] = results[1]
                    all_corrs[i,:,:] = results[3]
                    all_imags[i,:] = results[5]
                else:
                    good_asts[i] = False

        indxs, = np.where(good_asts)

        return (all_covs[indxs,:,:], all_biases[indxs,:],
                all_corrs[indxs,:,:], np.power(10.0,-0.4*all_imags[indxs,:]))

    def process_asts(self, filters):
        """
        Process all the AST results creating average biases and
        covariance matrices for each model SED.
        Also, prep for the interpolation by setting up the kd-tree
        
        Parameters
        ----------
        filters : filter names for the AST data
        
        Returns
        -------
        N/A.
        """
        results = self._calc_all_ast_cov(filters)

        self._cov_matrices = results[0]
        self._biases = results[1]
        self._corr_matrices = results[2]
        self._input_fluxes = results[3]

        print('building kd-tree...')
        self._kdtree = cKDTree(np.log10(self._input_fluxes))
        print('...done')

    def __call__(self, sedgrid, progress=True):
        """
        Interpolate the results of the ASTs on the model grid

        Parameters
        ----------
        sedgrid: beast.core.grid type
            model grid to interpolate AST results on

        Returns
        -------

        progress: bool, optional
            if set, display a progress bar
        """
        flux = sedgrid.seds
        n_models, n_filters = flux.shape
        n_offdiag = (((n_filters**2)-n_filters)/2)

        if n_filters != len(self.filters):
            raise AttributeError('the grid of models does not seem to' + 
                                 'be defined with the same number of filters') 

        bias = np.empty((n_models, n_filters), dtype=np.float64)
        sigmas = np.empty((n_models, n_filters), dtype=np.float64)
        icov_diag = np.empty((n_models, n_filters), dtype=np.float64)
        icov_offdiag = np.empty((n_models, n_offdiag), dtype=np.float64)
        q_norm = np.empty((n_models), dtype=np.float64)
        compl = np.empty((n_models, n_filters), dtype=float)

        n_models = 10
        if progress is True:
            it = Pbar(desc='Evaluating model').iterover(range(n_models))
        else:
            it = range(n_models)

        for i in it:
            # AST results are in vega normalized fluxes
            cur_flux = flux[i,:]/self.vega_flux

            # find the 10 nearest neighbors to the model SED
            result = self._kdtree.query(np.log10(cur_flux),10)

            dist = result[0]
            indxs = result[1]

            # compute the interpolated covariance matrix
            #    use the distances to generate weights for the sum
            dist_weights = 1.0/dist
            dist_weights /= np.sum(dist_weights)

            cur_cov_matrix = np.average(self._cov_matrices[indxs,:,:],
                                        axis=0,
                                        weights=dist_weights)

            # save the straight uncertainties
            sigmas[i,:] = np.sqrt(np.diagonal(cur_cov_matrix))

            # invert covariance matrix
            inv_cur_cov_matrix = np.linalg.inv(cur_cov_matrix)

            # save the diagnonal and packed version of non-diagonal terms
            m = 0
            icov_diag[i,n_filters-1] = inv_cur_cov_matrix[n_filters-1,
                                                          n_filters-1]
            for k in range(n_filters-1):
                icov_diag[i,k] = inv_cur_cov_matrix[k,k]
                for l in range(k+1,n_filters):
                    icov_offdiag[i,m] = inv_cur_cov_matrix[k,l]
                    m += 1

            # save the log of the determinat for normalization
            #   the ln(det) is calculated and saved as this is what will
            #   be used in the actual calculation
            #       norm = 1.0/sqrt(Q)
            det = np.linalg.slogdet(cur_cov_matrix)
            print(det)
            if det[0] <= 0:
                print('something bad happened')
                print('determinant of covarinace matrix is zero or negative')
                print(det)
            q_norm[i] = -0.5*det[1]

        return (bias, sigma, compl, q_norm, icov_diag, icov_offdiag)
        