
#####################################################################
# The patcher for factory ase.calculator.vasp.Vasp class            #
# will change the behavior of the POSCAR writer to use vasp5 format #
#####################################################################

from ase.calculators.vasp.create_input import GenerateVaspInput
from ase.calculators.vasp.create_input import bool_keys, int_keys, float_keys
from ase.calculators.vasp import Vasp
from pymatgen.io.vasp import Vasprun
from pymatgen.electronic_structure.bandstructure import Spin
import os
import os.path
import shutil
from ase.io import read
from .other_vasp import gen_line_path
import numpy

# Tell vasp calculator to write the POSCAR using vasp5 style


def _new_write_input(self, atoms, directory='./', direct=True, vasp5=True):
    from ase.io.vasp import write_vasp
    from os.path import join
    write_vasp(join(directory, 'POSCAR'),
               self.atoms_sorted,
               direct=direct,
               symbol_count=self.symbol_count, vasp5=vasp5)
    self.write_incar(atoms, directory=directory)
    self.write_potcar(directory=directory)
    self.write_kpoints(directory=directory)
    self.write_sort_file(directory=directory)

# Hot patch for the GenerateVaspInput class
GenerateVaspInput.write_input = _new_write_input


def _load_vasprun(self, filename="vasprun.xml"):
    self.vasprun = Vasprun(filename)

# read the bandgap from vasprun.xml


def _read_bandgap(self):
    if not hasattr(self, "vasprun"):
        self.load_vasprun()
    # From DOS
    dos = self.vasprun.complete_dos
    bg_dos = dos.get_gap()
    # From Band structure
    bs = self.vasprun.get_band_structure()
    bg_bs = bs.get_band_gap()
    # Return the bandgaps calculated by DOS or band structure
    return (bg_dos, bg_bs)


def _read_extern_stress(self, form="kB", filename="OUTCAR"):
    stress = None
    for line in open(filename):
        if line.find('external pressure') != -1:
            stress = line.split()[3]
            if form != "kB":
                # in GPa
                stress = stress * 0.1 * GPa
    return stress


def _copy_files(self, select_names=None,
                exclude_names=None,
                tag="tag"):
    # copy_file is supposed to be used only after the calculation!
    if hasattr(self, "tag"):
        tag = self.tag
    default_names = ["INCAR", "OUTCAR", "WAVECAR", "CONTCAR",
                     "WAVEDER", "DOSCAR", "vasprun.xml"]
    if exclude_names != None:
        tmp = [p for p in default_names if p not in exclude_names]
        default_names = tmp
    elif select_names != None:
        default_names = select_names

    for fname in default_names:
        if os.path.exists(fname):
            f_new = ".".join((fname, tag))
            shutil.copy(fname, f_new)

# Get the final potential from vasprun.xml


def _get_final_E(self, filename="vasprun.xml"):
    v = Vasprun(filename)
    fe = v.final_energy.real
    return fe

oldrun = Vasp.run
def _run(self):
    # Handle the incomplete BSE vasprun problem
    oldrun(self)
    if os.path.exists("vasprun.xml"):
        with open("vasprun.xml", "rb+") as f:
            f.seek(-12, 2)                        # to the -12
            s = f.read()
            s = s.decode("utf8")
            if s.strip() != "</modeling>":  # Not the last line
                f.seek(0, 2)                # To the last
                f.write(b"</modeling>\n")
                print("Warning! The vasprun.xml seems incomplete.")


# path for writing kpoints
# taken from jasp
def _write_kpoints(self, directory="", fname=None):
    """Write out the KPOINTS file.
    The KPOINTS file format is as follows:
    line 1: a comment
    line 2: number of kpoints
        n <= 0   Automatic kpoint generation
        n > 0    explicit number of kpoints
    line 3: kpt format
        if n > 0:
            C,c,K,k = cartesian coordinates
            anything else = reciprocal coordinates
        if n <= 0
            M,m,G,g for Monkhorst-Pack or Gamma grid
            anything else is a special case
    line 4: if n <= 0, the Monkhorst-Pack grid
        if n > 0, then a line per kpoint
    line 5: if n <=0 it is the gamma shift
    After the kpts may be tetrahedra, but we do now support that for
    now.
    """
    import numpy as np

    if fname is None:
        fname = os.path.join(directory, 'KPOINTS')

    p = self.input_params

    kpts = p.get('kpts', None)  # this is a list, or None
    # kpts_weight = p.get("kpts_weight", None)  # weights of the kpoints for BS

    if kpts is None:
        NKPTS = None
    elif len(np.array(kpts).shape) == 1:
        NKPTS = 0  # automatic
    else:
        NKPTS = len(p['kpts'])

    # figure out the mode
    if NKPTS == 0 and not p.get('gamma', None):
        MODE = 'm'  # automatic monkhorst-pack
    elif NKPTS == 0 and p.get('gamma', None):
        MODE = 'g'  # automatic gamma monkhorst pack
    # we did not trigger automatic kpoints
    elif p.get('kpts_nintersections', None) is not None:
        MODE = 'l'
    elif p.get('reciprocal', None) is True:
        MODE = 'r'
    else:
        MODE = 'c'

    with open(fname, 'w') as f:
        # line 1 - comment
        comm = 'KPOINTS created by Atomic Simulation Environment\n'
        if p.get("kpath", None) is not None:
            comm = "KPATH: {} \n".format(p.get("kpath", None))
        f.write(comm)
        # line 2 - number of kpts
        if MODE in ['c', 'k', 'm', 'g', 'r']:
            f.write('{}\n'.format(NKPTS))
        elif MODE in ['l']:  # line mode, default intersections is 10
            f.write('{}\n'.format(p.get('kpts_nintersections')))

        # line 3
        if MODE in ['m', 'g', 'l']:
            if MODE == 'm':
                f.write('Monkhorst-Pack\n')  # line 3
            elif MODE == 'g':
                f.write('Gamma\n')
            else:
                f.write("Line mode\n")
        elif MODE in ['c', 'k']:
            f.write('Cartesian\n')
        else:
            f.write('Reciprocal\n')

        # line 4
        if MODE in ['m', 'g']:
            f.write('{0:<9} {1:<9} {2:<9}\n'.format(*p.get('kpts', (1, 1, 1))))
        elif MODE in ['c', 'k', 'r']:
            for n in range(NKPTS):
                # I assume you know to provide the weights
                f.write('{0:<9} {1:<9} {2:<9} {3:<4}\n'.format(*p['kpts'][n]))
        elif MODE in ['l']:
            if p.get('reciprocal', None) is False:
                f.write('Cartesian\n')
            else:
                f.write('Reciprocal\n')
            for n in range(NKPTS):
                f.write('{0:<9} {1:<9} {2:<9} 1\n'.format(*p['kpts'][n]))

        # line 5 - only if we are in automatic mode
        if MODE in ['m', 'g']:
            if p.get('gamma', None):
                f.write('{0:<9} {1:<9} {2:<9}\n'.format(*p['gamma']))
            else:
                f.write('0.0 0.0 0.0\n')

# Patch method for get the atoms from previous calculation


def read_atoms_sorted(path=""):
    f_sort = os.path.join(path, 'ase-sort.dat')
    f_contcar = os.path.join(path, "CONTCAR")
    if os.path.isfile(f_sort):
        sort = []
        resort = []
        line = None
        with open(f_sort, 'r') as dat_sort:
            lines = dat_sort.readlines()
        for line in lines:
            data = line.split()
            sort.append(int(data[0]))
            resort.append(int(data[1]))
        atoms = read(f_contcar, format='vasp')[resort]
    else:
        atoms = read(f_contcar, format='vasp')
    return atoms


# Hot patch to the Vasp class
Vasp.read_bandgap = _read_bandgap
Vasp.load_vasprun = _load_vasprun
Vasp.read_extern_stress = _read_extern_stress
Vasp.copy_files = _copy_files
Vasp.read_final_E = _get_final_E
Vasp.write_kpoints = _write_kpoints
Vasp.run = _run

# Add missing keys
bool_keys += ["lusew",
              "ladder",
              "lhartree",
              "lpead",
              "lvdwexpansion",
              "lorbitalreal"]

int_keys += ["antires",
             "omegamax",
]

# Patching the vasprun.xml for BSE calculations
@property
def _converged_electronic(self):
    """
    Checks that electronic step convergence has been reached in the final
    ionic step
    """
    try:
        final_esteps = self.ionic_steps[-1]["electronic_steps"]
    except IndexError:
        return False            # no actual ionic steps
    if 'LEPSILON' in self.incar and self.incar['LEPSILON']:
        i = 1
        to_check = set(['e_wo_entrp', 'e_fr_energy', 'e_0_energy'])
        while set(final_esteps[i].keys()) == to_check:
            i += 1
        return i + 1 != self.parameters["NELM"]
    return len(final_esteps) < self.parameters["NELM"]

@property
def optical_transitions(self):
    # Get optical transitions of BSE calculation
    from xml.etree import ElementTree as ET
    import numpy
    ep = None
    for event, elem in ET.iterparse(self.filename):
        if ("name" in elem.attrib) and (elem.attrib["name"] == "opticaltransitions"):
            ep = elem
            break
    ot_array = []
    for v in ep:
        # print(v)
        ot_array.append(list(map(float, v.text.strip().split())))
    ot_array = numpy.array(ot_array)
    return ot_array


def distance(a, b, lattice=[[1, 0, 0],
                            [0, 1, 0],
                            [0, 0, 1]]):
    if len(a) != len(b):
        raise ValueError("a and b should be of same dimension!")
    a_ = [sum([lattice[i][j] * a[i] for i in range(len(a))]) for j in range(len(lattice))]
    b_ = [sum([lattice[i][j] * b[i] for i in range(len(b))]) for j in range(len(lattice))]
    par_dis = [(a_[i] - b_[i]) ** 2 for i in range(len(a))]
    return sum(par_dis) ** 0.5

def is_on_path(p, kpath, eps=1e-6,
               lattice=[[1, 0, 0],
                        [0, 1, 0],
                        [0, 0, 1]]):
    # kpath is a list of points
    flag = False
    tot_dis = 0
    for i in range(len(kpath) - 1):
        start = kpath[i]
        end = kpath[i + 1]
        # print(start, end)
        # print(distance(start, p),
        #       distance(p, end),
        #       distance(start, end))
        if abs(distance(start, p) + distance(p, end) \
               - distance(start, end)) < eps:
            flag = flag or True
            tot_dis = tot_dis + distance(start, p,
                                         lattice=lattice)

        if flag is True:
            return flag, tot_dis
        else:
            tot_dis = tot_dis + distance(start, end,
                                         lattice=lattice)
            
    return flag, tot_dis

def get_distance_nodes(kpath,
                       lattice=[[1, 0, 0],
                                [0, 1, 0],
                                [0, 0, 1]]):
    res = []
    tot_dis = 0
    res.append(0)
    for i in range(len(kpath) - 1):
        tot_dis = tot_dis + distance(kpath[i], kpath[i + 1],
                                     lattice=lattice)
        res.append(tot_dis)
    return res


# Get the eigenvalues from the k-points in the kpath
def get_bands_along_path(self,
                    kpath=None,
                    lattice_type=None):
    if (kpath is None):
        return self.eigenvalues[Spin.up]
    elif lattice_type is not None:
        path_nodes = gen_line_path(kpath,
                                   lattice_type,
                                   n_int=0)
        print(path_nodes)
        eig = self.eigenvalues[Spin.up]
        n_bands = eig.shape[1]
        kpts_path = []
        line_distance = []
        energies = [[] for i in range(n_bands)]
        cbm_kpt = None
        cbm_e = 1e4
        vbm_kpt = None
        vbm_e = -1e4
        lat_rec = self.lattice_rec.matrix
        # Generate the valid kpoints list
        # Get new bandgap
        for i in range(len(self.actual_kpoints)):
            kpt = self.actual_kpoints[i]
            # assert is_on_path(kpt, path_nodes) is True
            on_path, dist = is_on_path(kpt, path_nodes,
                                       lattice=lat_rec)
            if on_path:
                kpts_path.append(kpt)
                line_distance.append(dist)
                energy_occu = eig[i]
                prev_occu = 1
                prev_e = -1e4
                for j in range(len(energy_occu)):
                    e, occu = energy_occu[j]
                    energies[j].append(e)
                    if (prev_occu > 0) and (occu == 0):  # The cbm
                        if prev_e > vbm_e:
                            vbm_e = prev_e
                            vbm_kpt = kpt
                        if e < cbm_e:
                            cbm_e = e
                            cbm_kpt = kpt
                    prev_e = e
                    prev_occu = occu
        tot_dist = max(line_distance)
        print(tot_dist)
        nd = get_distance_nodes(path_nodes,
                                lattice=lat_rec)
        
        energies = numpy.array(energies)
        bandgap = cbm_e - vbm_e
        results = {"nbands": n_bands,
                   "bandgap": bandgap,
                   "band_energies": energies,
                   "kpts_path": kpts_path,
                   "line_dist": [d / tot_dist for d in line_distance],
                   "cbm": (cbm_e, cbm_kpt),
                   "vbm": (vbm_e, vbm_kpt),
                   "node_dist": [d / tot_dist for d in nd],
        }
        return results

Vasprun.converged_electronic = _converged_electronic
Vasprun.optical_transitions = optical_transitions
Vasprun.get_bands_along_path = get_bands_along_path
