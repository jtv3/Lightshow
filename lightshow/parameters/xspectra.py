from pathlib import Path
import json
import bz2
import base64
import shutil
from warnings import warn

from monty.json import MSONable
from pymatgen.io.pwscf import PWInput

from lightshow.parameters._base import _BaseParameters
from lightshow.common.kpoints import GenericEstimatorKpoints
from lightshow import (
    _get_CHPSP_DIRECTORY_from_environ,
    _get_PSP_DIRECTORY_from_environ,
)

XSPECTRA_DEFAULT_CARDS = {
    "QE": {
        "control": {"restart_mode": "from_scratch"},
        "electrons": {"conv_thr": 1e-08, "mixing_beta": 0.4},
        "system": {
            "degauss": 0.002,
            "ecutrho": 320,
            "ecutwfc": 40,
            "nspin": 1,
            "occupations": "smearing",
            "smearing": "gauss",
        },
    },
    "XS": {
        "cut_occ": {"cut_desmooth": 0.3},
        "input_xspectra": {
            "outdir": "../",
            "prefix": "pwscf",
            "xcheck_conv": 200,
            "xerror": 0.01,  #
            "xniter": 5000,
            "xcoordcrys": ".false.",
        },
        "kpts": {"kpts": "2 2 2", "shift": "0 0 0"},
        "plot": {
            "cut_occ_states": ".true.",
            "terminator": ".true.",
            "xemax": 70,
            "xemin": -15.0,
            "xnepoint": 400,
        },
    },
}


class XSpectraParameters(MSONable, _BaseParameters):
    """A one-stop-shop for all the different ways to modify input parameters
    for an XSpectra calculation.

    Parameters
    ----------
    cards : dict
        A dictionary of of the cards to be control the parameters in the
        OCEAN calculations. For example, one might wish to use something like

        .. code-block:: python

           cards = {
                    "QE": {
                        "control": {
                            "restart_mode": "from_scratch",
                        },
                        "electrons": {"conv_thr": 1e-08, "mixing_beta": 0.4},
                        "system": {
                            "degauss": 0.002,
                            "ecutrho": 320,
                            "ecutwfc": 40,
                            "nspin": 1,
                            "occupations": "smearing",
                            "smearing": "gauss",
                        },
                    },
                    "XS": {
                        "cut_occ": {"cut_desmooth": 0.3},
                        "input_xspectra": {
                            "outdir": "../",
                            "prefix": "pwscf",
                            "xcheck_conv": 200,
                            "xerror": 0.01,  #
                            "xniter": 5000,
                            "xcoordcrys": ".false.",
                       },
                        "kpts": {"kpts": "2 2 2", "shift": "0 0 0"},
                        "plot": {
                            "cut_occ_states": ".true.",
                            "terminator": ".true.",
                            "xemax": 70,
                            "xemin": -15.0,
                            "xnepoint": 400,
                        },
                    },
            }

    kpoints : lightshow.common.kpoints._BaseKpointsMethod
        The method for constructing he kpoints file from the structure. Should
        be a class with a ``__call__`` method defined. This method should take
        the structure as input and return a tuple corresponding to the kpoints
        density along each axis.
    psp_directory : os.PathLike, optional
        The location in which the neutral potential files for absorption atoms
        are stored. In the psp_directory, a cutoff table should also be
        provided. The cutoff table should have similar structure as the one for
        SSSP database. The name of the cutoff table needs to be given in
        cards['XS']['psp_cutoff_table']. If None, checks the  environment for
        ``XS_PSP_DIRECTORY``.
    chpsp_directory : os.PathLike, optional
        The location in which the core-hole potential files for absorption atoms
        are stored. Each element should have two files, e.g. "Ti.fch.upf" and
        "Core_Ti.wfc". "Ti.fch.upf" is the core-hole pesuodo potetial file and
        "Core_Ti.wfc" is the core electron wavefunction. The naming of the
        pseudopotentials and core electron wavefunction should follow the exact
        specific structure. If None, checks the environment for
        ``XS_CHPSP_DIRECTORY``.
    psp_cutoff_table  : str
        The name of the cutoff table to loop around the pseudo potentials, the
        format should be the same as the one for SSSP database. The cutoff
        table shoyld be placed under the psp_directory.
    psp_json : str
        The name of the compact pseudo potential database in a single json
        file, which can be generated by the packPsps method. If not None, the
        json file should be placed under the psp_directory.
    """

    @property
    def name(self):
        return self._name

    @property
    def cards(self):
        return self._cards

    def __init__(
        self,
        cards=XSPECTRA_DEFAULT_CARDS,
        kpoints=GenericEstimatorKpoints(cutoff=16.0, max_radii=50.0),
        chpsp_directory=None,
        psp_directory=None,
        psp_cutoff_table=None,
        psp_json=None,
        defaultConvPerAtom=1e-10,
        edge="K",
        name="XSpectra",
    ):
        # Default cards
        self._cards = cards

        # chpsp information
        if chpsp_directory is None:
            chpsp_directory = _get_CHPSP_DIRECTORY_from_environ()
        if chpsp_directory is None:
            warn(
                "chpsp_directory not provided, and XS_CHPSP_DIRECTORY not in "
                "the current environment variables. core-hole pseudo "
                "potential files will not be written."
            )
        self._chpsp_directory = chpsp_directory
        # psp information
        self._psp_cutoff_table = psp_cutoff_table
        self._psp_json = psp_json

        if psp_directory is None:
            psp_directory = _get_PSP_DIRECTORY_from_environ()

        self._psp_directory = psp_directory

        if psp_directory is None or self._psp_cutoff_table is None:
            warn(
                "psp_directory not provided XS_PSP_DIRECTORY not in the "
                "current environment variables OR psp_cutoff_table not "
                "provided. neutral pseudo potential files will not be written."
            )

        # Method for determining the kmesh
        self._kpoints = kpoints
        self._defaultConvPerAtom = defaultConvPerAtom
        self._edge = edge
        self._name = name

    def packPsps(self, pspJsonOut):
        """This method packs all the pseudo potentials the self._psp_directory
        into a single json file, whose name is given by ``pspJsonOut``. By using
        the condensed json file, the performance for writing pseudo potentials
        files is better than copying them from self._psp_directory to the
        working directory.

        Parameters
        ----------
        pspJsonOut : str
            Name of the output json file. Extension should be included.
        """
        cutofftable = Path(self._psp_directory) / Path(self._psp_cutoff_table)
        with open(cutofftable, "r") as f:
            inJSON = json.load(f)

        outJSON = dict()

        for element in inJSON:
            pspFile = Path(self._psp_directory) / Path(
                inJSON[element]["filename"]
            )
            with open(pspFile, "r") as f:
                pspString = f.read()

            outJSON[inJSON[element]["filename"]] = base64.b64encode(
                bz2.compress(pspString.encode("utf-8"), 9)
            ).decode("utf-8")

        with open(pspJsonOut, "w") as f:
            f.write(json.dumps(outJSON))

    def _unpackPsps(
        self,
        ecutwfc,
        ecutrho,
        symbols,
        folder,
    ):
        """Used to generate the pseudo potential files from the compact json
        file.

        Parameters
        ----------
        ecutwfc : float
            Energy cutoff for wave function
        ecutrho : float
            Energy cutoff for charge density
        symbols : list
            List of symbols for the element in the strcuture
        folder : str
            Name of the folder where the pseudo potential files will be
            recovered

        Returns
        -------
        psp : dict
            A dictionary with key as the name of element and value as the name
            of the pseudo potential file name.
        ecutwfc : float
            Energy cutoff for wave function
        ecutrho : float
            Energy cutoff for charge density
        """
        psp = {}
        sssp_fn = Path(self._psp_directoy) / Path(self._psp_cutoff_table)
        with open(sssp_fn, "r") as pspDatabaseFile:
            pspDatabase = json.load(pspDatabaseFile)
        minSymbols = set(symbols)
        for symbol in minSymbols:
            psp[symbol] = pspDatabase[symbol]["filename"]
            if ecutwfc < pspDatabase[symbol]["cutoff"]:
                ecutwfc = pspDatabase[symbol]["cutoff"]
            if ecutrho < pspDatabase[symbol]["rho_cutoff"]:
                ecutrho = pspDatabase[symbol]["rho_cutoff"]

        sssp_fn = Path(self._psp_directory) / Path(self._psp_json)
        with open(sssp_fn, "r") as p:
            pspJSON = json.load(p)
        for symbol in minSymbols:
            fileName = psp[symbol]
            pspString = bz2.decompress(base64.b64decode(pspJSON[fileName]))
            # print("Expected hash:  " + pspDatabase[symbol]["md5"])
            # print("Resultant hash: " + hashlib.md5(pspString).hexdigest())
            with open(folder / fileName, "w") as f:
                f.write(pspString.decode("utf-8"))

        return psp, ecutwfc, ecutrho

    def _write_xspectra_in(self, mode, iabs, dirs, xkvec):
        """construct input file for XSpectra calculation

        Parameters
        ----------
        mode : str
            "dipole" or "quadrupole"
        iabs : int
            The index of the absorbing element in scf calculation
        dirs : list
            Description of the polarization direction, e.g. [1,0,0]
            corresponds to the x direction
        xkvec : list
            Description of the k vectors for quadrupole calculation

        Returns
        -------
        string of the XSpectra input file
        """
        XSparams = self._cards["XS"]
        element = XSparams["element"]
        inp = [
            "&input_xspectra",
            "    calculation = 'xanes_%s'" % mode,
            "    edge = '" + XSparams["input_xspectra"]["edge"] + "'",
            "    prefix = 'pwscf'",
            "    outdir = '../'",
            "    xniter = " + str(XSparams["input_xspectra"]["xniter"]),
            "    xiabs = %d" % iabs,
            "    xerror = " + str(XSparams["input_xspectra"]["xerror"]),
            "    xcoordcrys = " + XSparams["input_xspectra"]["xcoordcrys"],
            "    xcheck_conv = "
            + str(XSparams["input_xspectra"]["xcheck_conv"]),
            "    xepsilon(1) = %d" % dirs[0],
            "    xepsilon(2) = %d" % dirs[1],
            "    xepsilon(3) = %d" % dirs[2],
        ]

        if mode == "quadrupole":
            inp += [
                "    xkvec(1) = %.10f" % xkvec[0],
                "    xkvec(2) = %.10f" % xkvec[1],
                "    xkvec(3) = %.10f" % xkvec[2],
            ]

        inp += [
            "/",
            "&plot",
            "    xnepoint = " + str(XSparams["plot"]["xnepoint"]),
            "    xemin = " + str(XSparams["plot"]["xemin"]),
            "    xemax = " + str(XSparams["plot"]["xemax"]),
            "    terminator = " + XSparams["plot"]["terminator"],
            "    cut_occ_states = " + XSparams["plot"]["cut_occ_states"],
            # use very small smearing value: 0.01 eV
            "    gamma_mode = 'constant'",
            "    xgamma = 0.01 ",
            "/",
        ]

        inp += [
            "&pseudos",
            f"    filecore = '../../Core_{element}.wfc'",
            "/",
            "&cut_occ",
            "    cut_desmooth = " + str(XSparams["cut_occ"]["cut_desmooth"]),
            "/",
            XSparams["kpts"]["kpts"] + " " + XSparams["kpts"]["shift"],
        ]
        return "\n".join(inp) + "\n"

    def write(self, target_directory, **kwargs):
        """Writes the input files for the provided structure and sites.

        Parameters
        ----------
        target_directory : os.PathLike
            The target directory to which to save the FEFF input files.
        **kwargs
            Must contain the ``structure_sc`` key (the
            :class:`pymatgen.core.structure.Structure` of interest), the
            ``sites`` key (a list of int, where each int corresponds to the
            site index in the supercell structure) and the ``index_mapping``
            key (a dictionary mapping the index between unit cell and supercell)

        Returns
        -------
        dict
            A dictionary containing the status and errors key. In the case of
            XSpectra, there are no possible errors at this stage other than
            critical ones that would cause program termination, so the returned
            object is always ``{"pass": True, "errors": dict()}``.
        """

        structure = kwargs["structure_sc"]
        sites = kwargs["sites"]
        index_mapping = kwargs["index_mapping"]

        target_directory = Path(target_directory)
        target_directory.mkdir(exist_ok=True, parents=True)
        symbols = [spec.symbol for spec in structure.species]
        # Obtain absorbing atom
        species = [
            structure[index_mapping[site]].specie.symbol for site in sites
        ]
        element = species[0]
        self._cards["XS"]["element"] = element
        self._cards["XS"]["input_xspectra"]["edge"] = self._edge
        symTarg = element
        # Estimate number of kpoints
        if len(structure.get_primitive_structure()) != len(structure):
            # use Gamma point for ground state calculations (es.in and gs.in)
            kpoints_scf = [1, 1, 1]
        else:
            kpoints_scf = self._kpoints(structure)

        kpoints_xas = self._kpoints(structure)

        self._cards["XS"]["kpts"][
            "kpts"
        ] = f"{kpoints_xas[0]} {kpoints_xas[1]} {kpoints_xas[2]}"
        # Determine the SCF? convergence threshold
        self._cards["QE"]["electrons"][
            "conv_thr"
        ] = self._defaultConvPerAtom * len(structure)
        # Get the psp data ready for the GS calculations; similar to SCF
        # (neutral) calculations in VASP
        # need to treat three different cases here

        ecutwfc = self._cards["QE"]["system"]["ecutwfc"]
        ecutrho = self._cards["QE"]["system"]["ecutrho"]

        if self._psp_directory is not None:
            try:
                psp_dict = json.load(
                    open(
                        Path(self._psp_directory) / Path(self._psp_cutoff_table)
                    )
                )
                psp = dict()
                for symbol in symbols:
                    psp_filename = psp_dict[symbol]["filename"]
                    psp[symbol] = psp_filename
                    shutil.copyfile(
                        Path(self._psp_directory) / Path(psp_filename),
                        target_directory / psp_filename,
                    )
                    if psp_dict[symbol]["cutoff_wfc"] > ecutwfc:
                        ecutwfc = psp_dict[symbol]["cutoff_wfc"]
                    if psp_dict[symbol]["cutoff_rho"] > ecutrho:
                        ecutrho = psp_dict[symbol]["cutoff_rho"]
            except FileNotFoundError:
                try:
                    psp, ecutwfc, ecutrho = self._unpackPsps(
                        ecutwfc,
                        ecutrho,
                        symbols,
                        target_directory,
                    )
                except FileNotFoundError:
                    warn(
                        "Some pseudo potential files are not present in "
                        f"f{self._psp_directory}"
                    )
                    self._psp_directory = None

        if self._psp_directory is None:
            psp = {symbol: symbol + ".upf" for symbol in symbols}

        self._cards["QE"]["system"]["ecutwfc"] = ecutwfc
        self._cards["QE"]["system"]["ecutrho"] = ecutrho
        self._cards["QE"]["control"]["pseudo_dir"] = "../"

        path = target_directory / "GS"
        path.mkdir(exist_ok=True, parents=True)
        gs_in = PWInput(
            structure,
            pseudo=psp,
            control=self._cards["QE"]["control"],
            system=self._cards["QE"]["system"],
            electrons=self._cards["QE"]["electrons"],
            kpoints_grid=kpoints_scf,
        )
        gs_in.write_file(path / "gs.in")

        psp[f"{element}+"] = f"{element}.fch.upf"  # psp2[i]
        # copy core-hole potential and core wfn to target folder
        if self._chpsp_directory is not None:
            try:
                shutil.copyfile(
                    self._chpsp_directory + f"/{element}.fch.upf",
                    target_directory / Path("{element}.fch.upf"),
                )
                shutil.copyfile(
                    self._chpsp_directory + f"/Core_{element}.wfc",
                    target_directory / Path("Core_{element}.wfc"),
                )
            except FileNotFoundError:
                warn(
                    f"{element}.fch.upf or Core_{element}.wfc not found "
                    f"in {self._chpsp_directory}"
                )

        # Determine iabs
        for i, j in enumerate(sorted(psp.keys())):
            if j == symTarg + "+":
                iabs = i + 1
        for site, specie in zip(sites, species):
            path = target_directory / Path(f"{site:03}_{specie}")
            path.mkdir(exist_ok=True, parents=True)

            structure[index_mapping[site]] = element + "+"
            self._cards["QE"]["control"]["pseudo_dir"] = "../"
            es_in = PWInput(
                structure,
                pseudo=psp,
                control=self._cards["QE"]["control"],
                system=self._cards["QE"]["system"],
                electrons=self._cards["QE"]["electrons"],
                kpoints_grid=kpoints_scf,
            )
            es_in.write_file(path / "es.in")
            structure[index_mapping[site]] = element

            # Deal with the dipole case only
            # notice I put the photonSymm in the folder, which is created by
            # John
            photons = list()
            photons.append({"dipole": [1, 0, 0, 1]})
            photons.append({"dipole": [0, 1, 0, 1]})
            photons.append({"dipole": [0, 0, 1, 1]})

            totalweight = 0
            for photon in photons:
                totalweight += photon["dipole"][3]

            photonCount = 0
            for photon in photons:
                photonCount += 1
                dir1 = photon["dipole"][0:3]
                dir2 = dir1
                weight = photon["dipole"][3] / totalweight
                mode = "dipole"

                xanesfolder = path / f"{mode}{photonCount}"
                xanesfolder.mkdir(parents=True, exist_ok=True)
                with open(xanesfolder / "xanes.in", "w") as f:
                    f.write(
                        self._write_xspectra_in(
                            mode,
                            iabs,
                            dir1,
                            dir2,
                        )
                    )

                with open(xanesfolder / "weight.txt", "w") as f:
                    f.write(str(weight) + "\n")

        return {"pass": True, "errors": dict()}
