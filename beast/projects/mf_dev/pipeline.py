"""
Model Pipeline
==============

Create a model grid:

    1. download isochrone(**pars)
    2. make spectra(osl)
    3. make seds(filters, **av_pars)

each step outputs results that are stored into <project>_<...>.<csv|fits>

TODO: make a function that takes user pars and return the pipeline instance
"""
from __future__ import print_function
from beast.external.ezpipe import Pipeline
from beast.external.ezpipe.helpers import task_decorator
from beast.external.eztables import Table
from beast.tools.helpers import chunks
from beast.tools.pbar import Pbar

import os

import datamodel
import noisemodel
from models import t_isochrones, t_spectra, t_seds
from fit import t_fit, t_summary_table


@task_decorator()
def t_get_obscat(project, obsfile=datamodel.obsfile,
                 distanceModulus=datamodel.distanceModulus,
                 filters=datamodel.filters,
                 *args, **kwargs):
    """ task that generates a data catalog object with the correct arguments

    Parameters
    ----------
    obsfile: str, optional (default datamodel.obsfile)
        observation file

    distanceModulus: float, optional (default datamodel.distanceModulus)
        distance modulus to correct the data from (in magitude)

    filters: sequence(str), optional, datamodel.filters
        seaquence of filters of the data

    returns
    -------
    project: str
        project id

    obs: PHATFluxCatalog
        observation catalog
    """
    obs = datamodel.get_obscat(obsfile, distanceModulus, filters,
                               *args, **kwargs)
    return project, obs


@task_decorator()
def t_project_dir(project, *args, **kwargs):
    """ Task that creates the project directory if necessary

    Parameters
    ----------
    project: str
        project name

    Returns
    -------
    dirname: str
        <project>/<project>

    Raises
    ------
    Exception
        if already exists a file that is not a directory
    """
    outdir = project
    if os.path.exists(outdir):
        if not os.path.isdir(outdir):
            raise Exception('Output directory "{0}" already exists but is not a directory'.format(outdir))
    else:
        os.mkdir(outdir)
    return '{0:s}/{1:s}'.format(outdir, project)


def prepare_individual_inputs(obsfile, chunksize=14000):
    """ Prepare N chuncks of observation input to be run in parallel

    Parameters
    ----------
    obsfile: fname
        input file containing observations

    chunksize: int
        number of sources per chunk of data
        (default number is based on maximized HDF5 node numbers/speed ratio)

    Returns
    -------
    obsfiles: sequence
        list of created files
        Files are generated with the given number of sources per individual catalog.
        Namings respects this convention: `<initial name>.<partk>.<initial_extension>`

    .. note::
        this function uses `beast.external.eztables` to read the catalog with
        the largest flexibility on the input format

    .. todo::
        loading the catalog could be optimized for memory usage.
        In practice this could also just be replaced by database queries.
    """
    if chunksize <= 0:
        return [obsfile]

    obs = Table(obsfile)
    # name will be <initial name>.<partk>.<initial_extension>
    outname = obsfile.split('.')
    outname = ('.'.join(outname[:-1]), outname[-1])

    obsfiles = []

    fpart = 0
    for chunk_slice in Pbar('Preparing input catalogs').iterover(chunks(range(obs.nrows), chunksize)):
        l_obs = obs[chunk_slice]
        l_file = '{0:s}.part{1:d}.{2:s}'.format(outname[0], fpart, outname[1])
        Table(l_obs).write(l_file)
        obsfiles.append(l_file)
        fpart += 1

    return obsfiles


def make_models():
    """ generates models from scratch

    1. creates the project directory,
    2. download isochrones,
    3. compute dust-free spectra from the isochrones
    4. apply dust and generate photometry to obtain the set of seds

    returns
    -------
    job: int
        job id

    (p, g): project and ModelGrid
        project identification
        Modelgrid instance constaining the collection of SEDs
    """
    # calling sequences
    iso_kwargs = dict(logtmin=datamodel.logt[0],
                      logtmax=datamodel.logt[1],
                      dlogt=datamodel.logt[2],
                      z=datamodel.z)

    spec_kwargs = dict(osl=datamodel.osl)

    seds_kwargs = dict(extLaw=datamodel.extLaw,
                       av=datamodel.avs,
                       rv=datamodel.rvs,
                       fbump=datamodel.fbumps)

    # make models if not there yet
    tasks_models = (t_project_dir,
                    t_isochrones(**iso_kwargs),
                    t_spectra(**spec_kwargs),
                    t_seds(datamodel.filters, **seds_kwargs) )

    models = Pipeline('make_models', tasks_models)
    job, (p, g) = models(datamodel.project)

    return job, (p, g)


def run_fit(project, g, ast=None, obsfile=None):
    """ Run the fit on specific inputs

    Parameters
    ----------
    project: str
        project id

    g: ModelGrid
        grid of SED models

    ast: tables.Table instance, optional
        ast table. if None, open file defined by `datamodel.ast`

    obsfile: str, optional
        observation catalog filename.
        if None, use file defined by `datamodel.obsfile`

    Returns
    -------
    job: int
        job identification

    (p, stat, obs, sedgrid): tuple
        p: project identification
        stat: summary table
        obs: observation catalog
        sedgrid: model grid
    """
    if obsfile is None:
        obsfile = datamodel.obsfile

    if ast is None:
        ast = noisemodel.get_noisemodelcat(datamodel.astfile)

    obscat_kwargs = dict(obsfile=obsfile,
                         distanceModulus=datamodel.distanceModulus,
                         filters=datamodel.filters)

    fit_kwargs = dict( threshold=-10 )

    stat_kwargs = dict( keys=None, method=None )

    tasks_fit = (t_project_dir,
                 t_get_obscat(**obscat_kwargs),
                 t_fit(g, ast, **fit_kwargs),
                 t_summary_table(g, **stat_kwargs) )

    fit_data = Pipeline('fit', tasks_fit)

    #run the job
    job, (p, stat, obs, sedgrid) = fit_data(project)

    return job, (p, stat, obs, sedgrid)


def run_chunk_fit(project, g, chunk, ast=None, obsfile=None):
    """
    Parameters
    ----------
    project: str
        project id

    g: ModelGrid
        grid of SED models

    chunk: int
        chunk number to run

    ast: tables.Table instance, optional
        ast table. if None, open file defined by `datamodel.ast`

    obsfile: str, optional
        observation catalog filename.
        if None, use file defined by `datamodel.obsfile`

    Returns
    -------
    job: int
        job identification

    (p, stat, obs, sedgrid): tuple
        p: project identification
        stat: summary table
        obs: observation catalog
        sedgrid: model grid
    """
    import glob

    if obsfile is None:
        obsfile = datamodel.obsfile

    if ast is None:
        ast = noisemodel.get_noisemodelcat(datamodel.astfile)

    obs_base = obsfile.split('.')
    obs_ext = obs_base[-1]
    obs_base = obs_base[:-1]

    lst = glob.glob('.'.join(obs_base) + '.part*.' + obs_ext)
    if len(lst) == 0:
        raise ValueError('cannot find any chunk. Did you run prepare_individual_inputs?')

    if chunk >= len(lst):
        print('Chunk not valid')

    l_obs = lst[chunk]
    print('running chunk {0:s}'.format(l_obs))

    #forcing output names
    outname = project[:]
    l_file = '{0:s}/{0:s}.part{1:d}'.format(outname, chunk)

    fit_kwargs = dict( threshold=-10, outname=l_file )

    stat_kwargs = dict( keys=None, method=None, outname=l_file)

    obscat_kwargs = dict(obsfile=l_obs,
                         distanceModulus=datamodel.distanceModulus,
                         filters=datamodel.filters)

    tasks_fit = (t_project_dir,
                 t_get_obscat(**obscat_kwargs),
                 t_fit(g, ast, **fit_kwargs),
                 t_summary_table(g, **stat_kwargs) )

    fit_data = Pipeline('fit', tasks_fit)

    job, (p, stat, obs, sedgrid) = fit_data(project)

    return job, (p, stat, obs, sedgrid)