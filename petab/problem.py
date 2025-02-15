"""PEtab Problem class"""

import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Optional, Union
from urllib.parse import unquote, urlparse, urlunparse
from warnings import warn

import libsbml
import pandas as pd

from . import (conditions, core, format_version, measurements, observables,
               parameter_mapping, parameters, sampling, sbml, yaml)
from .C import *  # noqa: F403

__all__ = ['Problem', 'get_default_condition_file_name',
           'get_default_measurement_file_name',
           'get_default_parameter_file_name',
           'get_default_sbml_file_name']


class Problem:
    """
    PEtab parameter estimation problem as defined by

    - SBML model
    - condition table
    - measurement table
    - parameter table
    - observables table

    Optionally it may contain visualization tables.

    Attributes:
        condition_df: PEtab condition table
        measurement_df: PEtab measurement table
        parameter_df: PEtab parameter table
        observable_df: PEtab observable table
        visualization_df: PEtab visualization table
        sbml_reader: Stored to keep object alive.
        sbml_document: Stored to keep object alive.
        sbml_model: PEtab SBML model
    """

    def __init__(self,
                 sbml_model: libsbml.Model = None,
                 sbml_reader: libsbml.SBMLReader = None,
                 sbml_document: libsbml.SBMLDocument = None,
                 condition_df: pd.DataFrame = None,
                 measurement_df: pd.DataFrame = None,
                 parameter_df: pd.DataFrame = None,
                 visualization_df: pd.DataFrame = None,
                 observable_df: pd.DataFrame = None):

        self.condition_df: Optional[pd.DataFrame] = condition_df
        self.measurement_df: Optional[pd.DataFrame] = measurement_df
        self.parameter_df: Optional[pd.DataFrame] = parameter_df
        self.visualization_df: Optional[pd.DataFrame] = visualization_df
        self.observable_df: Optional[pd.DataFrame] = observable_df

        self.sbml_reader: Optional[libsbml.SBMLReader] = sbml_reader
        self.sbml_document: Optional[libsbml.SBMLDocument] = sbml_document
        self.sbml_model: Optional[libsbml.Model] = sbml_model

    def __getstate__(self):
        """Return state for pickling"""
        state = self.__dict__.copy()

        # libsbml stuff cannot be serialized directly
        if self.sbml_model:
            sbml_document = self.sbml_model.getSBMLDocument()
            sbml_writer = libsbml.SBMLWriter()
            state['sbml_string'] = sbml_writer.writeSBMLToString(sbml_document)

        exclude = ['sbml_reader', 'sbml_document', 'sbml_model']
        for key in exclude:
            state.pop(key)

        return state

    def __setstate__(self, state):
        """Set state after unpickling"""
        # load SBML model from pickled string
        sbml_string = state.pop('sbml_string', None)
        if sbml_string:
            self.sbml_reader, self.sbml_document, self.sbml_model = \
                sbml.load_sbml_from_string(sbml_string)

        self.__dict__.update(state)

    @staticmethod
    def from_files(
            sbml_file: Union[str, Path, None] = None,
            condition_file: Union[str, Path, None] = None,
            measurement_file: Union[str, Path,
                                    Iterable[Union[str, Path]]] = None,
            parameter_file: Union[str, Path,
                                  Iterable[Union[str, Path]]] = None,
            visualization_files: Union[str, Path,
                                       Iterable[Union[str, Path]]] = None,
            observable_files: Union[str, Path,
                                    Iterable[Union[str, Path]]] = None
    ) -> 'Problem':
        """
        Factory method to load model and tables from files.

        Arguments:
            sbml_file: PEtab SBML model
            condition_file: PEtab condition table
            measurement_file: PEtab measurement table
            parameter_file: PEtab parameter table
            visualization_files: PEtab visualization tables
            observable_files: PEtab observables tables
        """

        sbml_model = sbml_document = sbml_reader = None
        condition_df = measurement_df = parameter_df = visualization_df = None
        observable_df = None

        if condition_file:
            condition_df = conditions.get_condition_df(condition_file)

        if measurement_file:
            # If there are multiple tables, we will merge them
            measurement_df = core.concat_tables(
                measurement_file, measurements.get_measurement_df)

        if parameter_file:
            parameter_df = parameters.get_parameter_df(parameter_file)

        if sbml_file:
            sbml_reader, sbml_document, sbml_model = \
                sbml.get_sbml_model(sbml_file)

        if visualization_files:
            # If there are multiple tables, we will merge them
            visualization_df = core.concat_tables(
                visualization_files, core.get_visualization_df)

        if observable_files:
            # If there are multiple tables, we will merge them
            observable_df = core.concat_tables(
                observable_files, observables.get_observable_df)

        return Problem(condition_df=condition_df,
                       measurement_df=measurement_df,
                       parameter_df=parameter_df,
                       observable_df=observable_df,
                       sbml_model=sbml_model,
                       sbml_document=sbml_document,
                       sbml_reader=sbml_reader,
                       visualization_df=visualization_df)

    @staticmethod
    def from_yaml(yaml_config: Union[Dict, Path, str]) -> 'Problem':
        """
        Factory method to load model and tables as specified by YAML file.

        Arguments:
            yaml_config: PEtab configuration as dictionary or YAML file name
        """
        if isinstance(yaml_config, Path):
            yaml_config = str(yaml_config)

        get_path = lambda filename: filename  # noqa: E731
        if isinstance(yaml_config, str):
            yaml_path = yaml_config
            yaml_config = yaml.load_yaml(yaml_config)

            # yaml_config may be path or URL
            path_url = urlparse(yaml_path)
            if not path_url.scheme or \
                    (path_url.scheme != 'file' and not path_url.netloc):
                # a regular file path string
                path_prefix = Path(yaml_path).parent
                get_path = lambda filename: \
                    path_prefix / filename  # noqa: E731
            else:
                # a URL
                # extract parent path from
                url_path = unquote(urlparse(yaml_path).path)
                parent_path = str(PurePosixPath(url_path).parent)
                path_prefix = urlunparse(
                    (path_url.scheme, path_url.netloc, parent_path,
                     path_url.params, path_url.query, path_url.fragment)
                )
                # need "/" on windows, not "\"
                get_path = lambda filename: \
                    f"{path_prefix}/{filename}"  # noqa: E731

        if yaml.is_composite_problem(yaml_config):
            raise ValueError('petab.Problem.from_yaml() can only be used for '
                             'yaml files comprising a single model. '
                             'Consider using '
                             'petab.CompositeProblem.from_yaml() instead.')

        if yaml_config[FORMAT_VERSION] != format_version.__format_version__:
            raise ValueError("Provided PEtab files are of unsupported version"
                             f"{yaml_config[FORMAT_VERSION]}. Expected "
                             f"{format_version.__format_version__}.")

        problem0 = yaml_config['problems'][0]

        yaml.assert_single_condition_and_sbml_file(problem0)

        if isinstance(yaml_config[PARAMETER_FILE], list):
            parameter_file = [
                get_path(f) for f in yaml_config[PARAMETER_FILE]
            ]
        else:
            parameter_file = get_path(yaml_config[PARAMETER_FILE])

        return Problem.from_files(
            sbml_file=get_path(problem0[SBML_FILES][0]),
            measurement_file=[get_path(f)
                              for f in problem0[MEASUREMENT_FILES]],
            condition_file=get_path(problem0[CONDITION_FILES][0]),
            parameter_file=parameter_file,
            visualization_files=[
                get_path(f) for f in problem0.get(VISUALIZATION_FILES, [])],
            observable_files=[
                get_path(f) for f in problem0.get(OBSERVABLE_FILES, [])]
        )

    @staticmethod
    def from_folder(folder: str, model_name: str = None) -> 'Problem':
        """
        Factory method to use the standard folder structure
        and file names, i.e.

        ::

            ${model_name}/
              +-- experimentalCondition_${model_name}.tsv
              +-- measurementData_${model_name}.tsv
              +-- model_${model_name}.xml
              +-- parameters_${model_name}.tsv

        Arguments:
            folder:
                Path to the directory in which the files are located.
            model_name:
                If specified, overrides the model component in the file names.
                Defaults to the last component of ``folder``.
        """
        warn("This function will be removed in future releases. "
             "Consider using a PEtab YAML file for grouping files",
             DeprecationWarning)

        folder = os.path.abspath(folder)
        if model_name is None:
            model_name = os.path.split(folder)[-1]

        return Problem.from_files(
            condition_file=get_default_condition_file_name(model_name, folder),
            measurement_file=get_default_measurement_file_name(model_name,
                                                               folder),
            parameter_file=get_default_parameter_file_name(model_name, folder),
            sbml_file=get_default_sbml_file_name(model_name, folder),
        )

    @staticmethod
    def from_combine(filename: Union[Path, str]) -> 'Problem':
        """Read PEtab COMBINE archive (http://co.mbine.org/documents/archive).

        See also :py:func:`petab.create_combine_archive`.

        Arguments:
            filename: Path to the PEtab-COMBINE archive

        Returns:
            A :py:class:`petab.Problem` instance.
        """
        # function-level import, because module-level import interfered with
        # other SWIG interfaces
        try:
            import libcombine
        except ImportError:
            raise ImportError(
                "To use PEtab's COMBINE functionality, libcombine "
                "(python-libcombine) must be installed.")

        archive = libcombine.CombineArchive()
        if archive.initializeFromArchive(str(filename)) is None:
            print(f"Invalid Combine Archive: {filename}")
            return None

        with tempfile.TemporaryDirectory() as tmpdirname:
            archive.extractTo(tmpdirname)
            problem = Problem.from_yaml(
                os.path.join(tmpdirname,
                             archive.getMasterFile().getLocation()))
        archive.cleanUp()

        return problem

    def to_files_generic(
        self,
        prefix_path: Union[str, Path],
    ) -> str:
        """Save a PEtab problem to generic file names.

        The PEtab problem YAML file is always created. PEtab data files are
        only created if the PEtab problem contains corresponding data (e.g. a
        PEtab visualization TSV file is only created if the PEtab problem has
        one).

        Arguments:
            prefix_path:
                Specify a prefix to all paths, to avoid specifying the
                prefix for all paths individually. NB: the prefix is added to
                paths before `relative_paths` is handled downstream in
                `petab.yaml.create_problem_yaml`.

        Returns:
            The path to the PEtab problem YAML file.
        """
        prefix_path = Path(prefix_path)

        # Generate generic filenames for data tables in the PEtab problem that
        # contain data.
        filenames = {}
        for table_name in [
            'condition',
            'measurement',
            'parameter',
            'observable',
            'visualization',
        ]:
            if getattr(self, f'{table_name}_df') is not None:
                filenames[f'{table_name}_file'] = f'{table_name}s.tsv'

        if self.sbml_document is not None:
            filenames['sbml_file'] = 'model.xml'

        filenames['yaml_file'] = 'problem.yaml'

        self.to_files(**filenames, prefix_path=prefix_path)

        if prefix_path is None:
            return filenames['yaml_file']
        return str(prefix_path / filenames['yaml_file'])

    def to_files(self,
                 sbml_file: Union[None, str, Path] = None,
                 condition_file: Union[None, str, Path] = None,
                 measurement_file: Union[None, str, Path] = None,
                 parameter_file: Union[None, str, Path] = None,
                 visualization_file: Union[None, str, Path] = None,
                 observable_file: Union[None, str, Path] = None,
                 yaml_file: Union[None, str, Path] = None,
                 prefix_path: Union[None, str, Path] = None,
                 relative_paths: bool = True,
                 ) -> None:
        """
        Write PEtab tables to files for this problem

        Writes PEtab files for those entities for which a destination was
        passed.

        NOTE: If this instance was created from multiple measurement or
        visualization tables, they will be merged and written to a single file.

        Arguments:
            sbml_file: SBML model destination
            condition_file: Condition table destination
            measurement_file: Measurement table destination
            parameter_file: Parameter table destination
            visualization_file: Visualization table destination
            observable_file: Observables table destination
            yaml_file: YAML file destination
            prefix_path:
                Specify a prefix to all paths, to avoid specifying the
                prefix for all paths individually. NB: the prefix is added to
                paths before `relative_paths` is handled.
            relative_paths:
                whether all paths in the YAML file should be
                relative to the location of the YAML file. If `False`, then
                paths are left unchanged.

        Raises:
            ValueError:
                If a destination was provided for a non-existing entity.
        """
        if prefix_path is not None:
            prefix_path = Path(prefix_path)

            def add_prefix(path0: Union[None, str, Path]) -> str:
                if path0 is None:
                    return path0
                return str(prefix_path / path0)

            sbml_file = add_prefix(sbml_file)
            condition_file = add_prefix(condition_file)
            measurement_file = add_prefix(measurement_file)
            parameter_file = add_prefix(parameter_file)
            observable_file = add_prefix(observable_file)
            visualization_file = add_prefix(visualization_file)
            yaml_file = add_prefix(yaml_file)

        if sbml_file:
            if self.sbml_document is not None:
                sbml.write_sbml(self.sbml_document, sbml_file)
            else:
                raise ValueError("Unable to save SBML model with no "
                                 "sbml_doc set.")

        def error(name: str) -> ValueError:
            return ValueError(f"Unable to save non-existent {name} table")

        if condition_file:
            if self.condition_df is not None:
                conditions.write_condition_df(self.condition_df,
                                              condition_file)
            else:
                raise error("condition")

        if measurement_file:
            if self.measurement_df is not None:
                measurements.write_measurement_df(self.measurement_df,
                                                  measurement_file)
            else:
                raise error("measurement")

        if parameter_file:
            if self.parameter_df is not None:
                parameters.write_parameter_df(self.parameter_df,
                                              parameter_file)
            else:
                raise error("parameter")

        if observable_file:
            if self.observable_df is not None:
                observables.write_observable_df(self.observable_df,
                                                observable_file)
            else:
                raise error("observable")

        if visualization_file:
            if self.visualization_df is not None:
                core.write_visualization_df(self.visualization_df,
                                            visualization_file)
            else:
                raise error("visualization")

        if yaml_file:
            yaml.create_problem_yaml(sbml_file, condition_file,
                                     measurement_file, parameter_file,
                                     observable_file, yaml_file,
                                     visualization_file,
                                     relative_paths=relative_paths,)

    def get_optimization_parameters(self):
        """
        Return list of optimization parameter IDs.

        See :py:func:`petab.parameters.get_optimization_parameters`.
        """
        return parameters.get_optimization_parameters(self.parameter_df)

    def get_optimization_parameter_scales(self):
        """
        Return list of optimization parameter scaling strings.

        See :py:func:`petab.parameters.get_optimization_parameters`.
        """
        return parameters.get_optimization_parameter_scaling(self.parameter_df)

    def get_model_parameters(self):
        """See :py:func:`petab.sbml.get_model_parameters`"""
        return sbml.get_model_parameters(self.sbml_model)

    def get_observables(self, remove: bool = False):
        """
        Returns dictionary of observables definitions.

        See :py:func:`petab.assignment_rules_to_dict` for details.
        """
        warn("This function will be removed in future releases.",
             DeprecationWarning)

        return sbml.get_observables(sbml_model=self.sbml_model, remove=remove)

    def get_observable_ids(self):
        """
        Returns dictionary of observable ids.
        """
        return list(self.observable_df.index)

    def get_sigmas(self, remove: bool = False):
        """
        Return dictionary of observableId => sigma as defined in the SBML
        model.

        This does not include parameter mappings defined in the measurement
        table.
        """
        warn("This function will be removed in future releases.",
             DeprecationWarning)

        return sbml.get_sigmas(sbml_model=self.sbml_model, remove=remove)

    def get_noise_distributions(self):
        """
        See :py:func:`petab.get_noise_distributions`.
        """
        return measurements.get_noise_distributions(
            measurement_df=self.measurement_df)

    def _apply_mask(self, v: List, free: bool = True, fixed: bool = True):
        """Apply mask of only free or only fixed values.

        Parameters
        ----------
        v:
            The full vector the mask is to be applied to.
        free:
            Whether to return free parameters, i.e. parameters to estimate.
        fixed:
            Whether to return fixed parameters, i.e. parameters not to
            estimate.

        Returns
        -------
        The reduced vector with applied mask.
        """
        if not free and not fixed:
            return []
        if not free:
            return [v[ix] for ix in self.x_fixed_indices]
        if not fixed:
            return [v[ix] for ix in self.x_free_indices]
        return v

    def get_x_ids(self, free: bool = True, fixed: bool = True):
        """Generic function to get parameter ids.

        Parameters
        ----------
        free:
            Whether to return free parameters, i.e. parameters to estimate.
        fixed:
            Whether to return fixed parameters, i.e. parameters not to
            estimate.

        Returns
        -------
        The parameter IDs.
        """
        v = list(self.parameter_df.index.values)
        return self._apply_mask(v, free=free, fixed=fixed)

    @property
    def x_ids(self) -> List[str]:
        """Parameter table parameter IDs"""
        return self.get_x_ids()

    @property
    def x_free_ids(self) -> List[str]:
        """Parameter table parameter IDs, for free parameters."""
        return self.get_x_ids(fixed=False)

    @property
    def x_fixed_ids(self) -> List[str]:
        """Parameter table parameter IDs, for fixed parameters."""
        return self.get_x_ids(free=False)

    def get_x_nominal(self, free: bool = True, fixed: bool = True,
                      scaled: bool = False):
        """Generic function to get parameter nominal values.

        Parameters
        ----------
        free:
            Whether to return free parameters, i.e. parameters to estimate.
        fixed:
            Whether to return fixed parameters, i.e. parameters not to
            estimate.
        scaled:
            Whether to scale the values according to the parameter scale,
            or return them on linear scale.

        Returns
        -------
        The parameter nominal values.
        """
        v = list(self.parameter_df[NOMINAL_VALUE])
        if scaled:
            v = list(parameters.map_scale(
                v, self.parameter_df[PARAMETER_SCALE]))
        return self._apply_mask(v, free=free, fixed=fixed)

    @property
    def x_nominal(self) -> List:
        """Parameter table nominal values"""
        return self.get_x_nominal()

    @property
    def x_nominal_free(self) -> List:
        """Parameter table nominal values, for free parameters."""
        return self.get_x_nominal(fixed=False)

    @property
    def x_nominal_fixed(self) -> List:
        """Parameter table nominal values, for fixed parameters."""
        return self.get_x_nominal(free=False)

    @property
    def x_nominal_scaled(self) -> List:
        """Parameter table nominal values with applied parameter scaling"""
        return self.get_x_nominal(scaled=True)

    @property
    def x_nominal_free_scaled(self) -> List:
        """Parameter table nominal values with applied parameter scaling,
        for free parameters."""
        return self.get_x_nominal(fixed=False, scaled=True)

    @property
    def x_nominal_fixed_scaled(self) -> List:
        """Parameter table nominal values with applied parameter scaling,
        for fixed parameters."""
        return self.get_x_nominal(free=False, scaled=True)

    def get_lb(self, free: bool = True, fixed: bool = True,
               scaled: bool = False):
        """Generic function to get lower parameter bounds.

        Parameters
        ----------
        free:
            Whether to return free parameters, i.e. parameters to estimate.
        fixed:
            Whether to return fixed parameters, i.e. parameters not to
            estimate.
        scaled:
            Whether to scale the values according to the parameter scale,
            or return them on linear scale.

        Returns
        -------
        The lower parameter bounds.
        """
        v = list(self.parameter_df[LOWER_BOUND])
        if scaled:
            v = list(parameters.map_scale(
                v, self.parameter_df[PARAMETER_SCALE]))
        return self._apply_mask(v, free=free, fixed=fixed)

    @property
    def lb(self) -> List:
        """Parameter table lower bounds."""
        return self.get_lb()

    @property
    def lb_scaled(self) -> List:
        """Parameter table lower bounds with applied parameter scaling"""
        return self.get_lb(scaled=True)

    def get_ub(self, free: bool = True, fixed: bool = True,
               scaled: bool = False):
        """Generic function to get upper parameter bounds.

        Parameters
        ----------
        free:
            Whether to return free parameters, i.e. parameters to estimate.
        fixed:
            Whether to return fixed parameters, i.e. parameters not to
            estimate.
        scaled:
            Whether to scale the values according to the parameter scale,
            or return them on linear scale.

        Returns
        -------
        The upper parameter bounds.
        """
        v = list(self.parameter_df[UPPER_BOUND])
        if scaled:
            v = list(parameters.map_scale(
                v, self.parameter_df[PARAMETER_SCALE]))
        return self._apply_mask(v, free=free, fixed=fixed)

    @property
    def ub(self) -> List:
        """Parameter table upper bounds"""
        return self.get_ub()

    @property
    def ub_scaled(self) -> List:
        """Parameter table upper bounds with applied parameter scaling"""
        return self.get_ub(scaled=True)

    @property
    def x_free_indices(self) -> List[int]:
        """Parameter table estimated parameter indices."""
        estimated = list(self.parameter_df[ESTIMATE])
        return [j for j, val in enumerate(estimated) if val != 0]

    @property
    def x_fixed_indices(self) -> List[int]:
        """Parameter table non-estimated parameter indices."""
        estimated = list(self.parameter_df[ESTIMATE])
        return [j for j, val in enumerate(estimated) if val == 0]

    def get_simulation_conditions_from_measurement_df(self):
        """See petab.get_simulation_conditions"""
        return measurements.get_simulation_conditions(self.measurement_df)

    def get_optimization_to_simulation_parameter_mapping(
            self,
            warn_unmapped: bool = True,
            scaled_parameters: bool = False,
            allow_timepoint_specific_numeric_noise_parameters:
            bool = False,
    ):
        """
        See get_simulation_to_optimization_parameter_mapping.
        """
        return parameter_mapping\
            .get_optimization_to_simulation_parameter_mapping(
                self.condition_df,
                self.measurement_df,
                self.parameter_df,
                self.observable_df,
                self.sbml_model,
                warn_unmapped=warn_unmapped,
                scaled_parameters=scaled_parameters,
                allow_timepoint_specific_numeric_noise_parameters=  # noqa: E251,E501
                allow_timepoint_specific_numeric_noise_parameters
            )

    def create_parameter_df(self, *args, **kwargs):
        """Create a new PEtab parameter table

        See :py:func:`create_parameter_df`.
        """
        return parameters.create_parameter_df(
            self.sbml_model,
            self.condition_df,
            self.observable_df,
            self.measurement_df,
            *args, **kwargs)

    def sample_parameter_startpoints(self, n_starts: int = 100):
        """Create starting points for optimization

        See :py:func:`petab.sample_parameter_startpoints`.
        """
        return sampling.sample_parameter_startpoints(
            self.parameter_df, n_starts=n_starts)

    def unscale_parameters(
        self,
        x_dict: Dict[str, float],
    ) -> Dict[str, float]:
        """Unscale parameter values.

        Parameters
        ----------
        x_dict:
            Keys are parameter IDs in the PEtab problem, values are scaled
            parameter values.

        Returns
        -------
        The unscaled parameter values.
        """
        return {
            parameter_id: parameters.unscale(
                parameter_value,
                self.parameter_df[PARAMETER_SCALE][parameter_id],
            )
            for parameter_id, parameter_value in x_dict.items()
        }

    def scale_parameters(
        self,
        x_dict: Dict[str, float],
    ) -> Dict[str, float]:
        """Scale parameter values.

        Parameters
        ----------
        x_dict:
            Keys are parameter IDs in the PEtab problem, values are unscaled
            parameter values.

        Returns
        -------
        The scaled parameter values.
        """
        return {
            parameter_id: parameters.scale(
                parameter_value,
                self.parameter_df[PARAMETER_SCALE][parameter_id],
            )
            for parameter_id, parameter_value in x_dict.items()
        }


def get_default_condition_file_name(model_name: str, folder: str = ''):
    """Get file name according to proposed convention"""
    warn("This function will be removed in future releases. ",
         DeprecationWarning)

    return os.path.join(folder, f"experimentalCondition_{model_name}.tsv")


def get_default_measurement_file_name(model_name: str, folder: str = ''):
    """Get file name according to proposed convention"""
    warn("This function will be removed in future releases. ",
         DeprecationWarning)

    return os.path.join(folder, f"measurementData_{model_name}.tsv")


def get_default_parameter_file_name(model_name: str, folder: str = ''):
    """Get file name according to proposed convention"""
    warn("This function will be removed in future releases. ",
         DeprecationWarning)

    return os.path.join(folder, f"parameters_{model_name}.tsv")


def get_default_sbml_file_name(model_name: str, folder: str = ''):
    """Get file name according to proposed convention"""
    warn("This function will be removed in future releases. ",
         DeprecationWarning)

    return os.path.join(folder, f"model_{model_name}.xml")
