# -*- coding: utf-8 -*-
"""
Created on Sat Apr 15 15:35:51 2017

@author: Calil
"""

import sys
import numpy as np
import matplotlib.pyplot as plt

from sharc.antenna.antenna_element_imt_m2101 import AntennaElementImtM2101
from sharc.antenna.antenna_element_imt_f1336 import AntennaElementImtF1336
from sharc.antenna.antenna_element_imt_const import AntennaElementImtConst
from sharc.antenna.antenna import Antenna
from sharc.support.enumerations import StationType
from sharc.support.named_tuples import AntennaPar
from sharc.parameters.imt.parameters_antenna_imt import ParametersAntennaImt


class AntennaBeamformingImt(Antenna):
    """
    Implements an antenna array

    Attributes
    ----------
        azimuth (float): physical azimuth inclination
        elevation (float): physical elevation inclination
        element (AntennaElementImt): antenna element
        n_rows (int): number of rows in array
        n_cols (int): number of columns in array
        dh (float): horizontal element spacing over wavelenght (d/lambda)
        dv (float): vertical element spacing over wavelenght (d/lambda)
        beams_list (list): vertical and horizontal tilts of beams
        normalize (bool): if normalization is applied
        norm_data (dict): data used for beamforming normalization
        adj_correction_factor (float): correction factor for adjacent channel
            single element pattern
        co_correction_factor (2D np.array): correction factor for co-channel
            antenna array pattern for given beam pointing direction
        resolution (float): beam pointing resolution [deg] of co-channel
            correction factor array
        minimum_array_gain (float): minimum array gain for beamforming
    """

    def __init__(self, par: AntennaPar, azimuth: float, elevation: float):
        """
        Constructs an AntennaBeamformingImt object.
        Does not receive angles in local coordinate system.
        Elevation taken with x axis as reference.

        Parameters
        ---------
            param (AntennaPar): antenna IMT parameters
            azimuth (float): antenna's physical azimuth inclination
            elevation (float): antenna's physical elevation inclination
                referenced in the x axis
        """
        super().__init__()
        self.param = par

        if (par.element_pattern).upper() == "M2101":
            self.element = AntennaElementImtM2101(par)
        elif (par.element_pattern).upper() == "F1336":
            self.element = AntennaElementImtF1336(par)
        elif (par.element_pattern).upper() == "FIXED":
            self.element = AntennaElementImtConst(par)
        else:
            sys.stderr.write(
                f"ERROR\nantenna element type {par.element_pattern} not supported",
            )
            sys.exit(1)

        self.azimuth = azimuth
        self.elevation = elevation
        self._calculate_rotation_matrix()
        self.minimum_array_gain = par.minimum_array_gain

        self.n_rows = par.n_rows
        self.n_cols = par.n_columns
        self.dh = par.element_horiz_spacing
        self.dv = par.element_vert_spacing

        self.adjacent_antenna_model = par.adjacent_antenna_model

        # Beamforming normalization
        self.normalize = par.normalization
        self.co_correction_factor_list = []
        self.adj_correction_factor = 0.0
        if self.normalize:
            # Load co-channel data
            self.norm_data = par.normalization_data
            self.adj_correction_factor = self.norm_data["correction_factor_adj_channel"]
            self.co_correction_factor = self.norm_data["correction_factor_co_channel"]
            self.resolution = self.norm_data["resolution"]

    def add_beam(self, phi_etilt: float, theta_etilt: float):
        """
        Add new beam to antenna.
        Does not receive angles in local coordinate system.
        Theta taken with z axis as reference.

        Parameters
        ----------
            phi_etilt (float): azimuth electrical tilt angle [degrees]
            theta_etilt (float): elevation electrical tilt angle [degrees]
        """
        phi, theta = self.to_local_coord(phi_etilt, theta_etilt)
        self.beams_list.append(
            (np.ndarray.item(phi), np.ndarray.item(theta - 90)),
        )
        self.w_vec_list.append(self._weight_vector(phi, theta - 90))

        if self.normalize:
            lin = int(phi / self.resolution)
            col = int(theta / self.resolution)
            self.co_correction_factor_list.append(
                self.co_correction_factor[lin, col],
            )
        else:
            self.co_correction_factor_list.append(0.0)

    def calculate_gain(self, *args, **kwargs) -> np.array:
        """
        Calculates the gain in the given direction.
        Does not receive angles in local coordinate system.
        Theta taken with z axis as reference.

        Parameters
        ----------
        phi_vec (np.array): azimuth angles [degrees]
        theta_vec (np.array): elevation angles [degrees]
        beam_l (np.array of int): optional. Index of beams for gain calculation
                Default is -1, which corresponds to the beam of maximum gain in
                given direction.
        co_channel (bool): optional, default is True. Indicates whether the
                antenna array pattern (co-channel case), or the element pattern
                (adjacent channel case) will be used for gain calculation.

        Returns
        -------
        gains (np.array): gain corresponding to each of the given directions.
        """
        phi_vec = np.asarray(kwargs["phi_vec"])
        theta_vec = np.asarray(kwargs["theta_vec"])
        station_type = kwargs.get("station_type", None)
        #print(f"Tipo de estación: {station_type}")
        # Check if antenna gain has to be calculated on the co-channel or
        # on the adjacent channel
        if "co_channel" in kwargs.keys():
            co_channel = kwargs["co_channel"]
        else:
            co_channel = True

        # If gain has to be calculated on the adjacent channel, then check whether
        # to use beamforming or single element pattern.
        # Both options are explicitly written in order to improve readability
        if not co_channel:
            if self.adjacent_antenna_model == "SINGLE_ELEMENT":
                co_channel = False
            elif self.adjacent_antenna_model == "BEAMFORMING":
                co_channel = True
            else:
                sys.stderr.write(
                    "ERROR\nInvalid antenna pattern for adjacent channel calculations: " +
                    self.adjacent_antenna_model,
                )
                sys.exit(1)

        correction_factor_idx = None
        if "beams_l" in kwargs.keys():
            beams_l = np.asarray(kwargs["beams_l"], dtype=int)
            correction_factor = self.co_correction_factor_list
            correction_factor_idx = beams_l
        else:
            beams_l = -1 * np.ones_like(phi_vec)
            if co_channel:
                if self.normalize:
                    lin_f = phi_vec / self.resolution
                    col_f = theta_vec / self.resolution
                    lin = lin_f.astype(int)
                    col = col_f.astype(int)
                    correction_factor = self.co_correction_factor[lin, col]
                else:
                    correction_factor = np.zeros_like(phi_vec)
                correction_factor_idx = [
                    i for i in range(len(correction_factor))
                ]

        lo_phi_vec, lo_theta_vec = self.to_local_coord(phi_vec, theta_vec)

        n_direct = len(lo_theta_vec)

        gains = np.zeros(n_direct)

        if co_channel:
            for g in range(n_direct):
                gains[g] = self._beam_gain(
                    lo_phi_vec[g], lo_theta_vec[g],
                    beams_l[g], station_type=station_type)\
                    + correction_factor[correction_factor_idx[g]]
        else:
            for g in range(n_direct):
                gains[g] = self.element.element_pattern(
                    lo_phi_vec[g],
                    lo_theta_vec[g],
                )\
                    + self.adj_correction_factor

        gains = np.maximum(gains, self.minimum_array_gain)

        return gains

    def reset_beams(self):
        """Reset beams lists
        """
        self.beams_list = []
        self.w_vec_list = []
        self.co_correction_factor_list = []

    def _super_position_vector(self, phi: float, theta: float) -> np.array:
        """
        Calculates super position vector.
        Angles are in the local coordinate system.

        Parameters
        ----------
            theta (float): elevation angle [degrees]
            phi (float): azimuth angle [degrees]

        Returns
        -------
            v_vec (np.array): superposition vector
        """
        #print("phi", phi)
        r_phi = np.deg2rad(phi)
        r_theta = np.deg2rad(theta)
        #print("Theta deg revisar: ", theta)
        n = np.arange(self.n_rows) + 1
        m = np.arange(self.n_cols) + 1

        exp_arg = (n[:, np.newaxis] - 1) * self.dv * np.cos(r_theta) + \
                  (m - 1) * self.dh * np.sin(r_theta) * np.sin(r_phi)

        v_vec = np.exp(2 * np.pi * 1.0j * exp_arg)
        #print("v_vec from function ", v_vec)
        return v_vec

    def _weight_vector(self, phi_tilt: float, theta_tilt: float) -> np.array:
        """
        Calculates super position vector.
        Angles are in the local coordinate system.

        Parameters
        ----------
            phi_tilt (float): electrical horizontal steering [degrees]
            theta_tilt (float): electrical down-tilt steering [degrees]

        Returns
        -------
            w_vec (np.array): weighting vector
        """
        #print("degrados phi_tilt", phi_tilt)
        r_phi = np.deg2rad(phi_tilt)
        r_theta = np.deg2rad(theta_tilt)
        #print("r_phi: ", r_phi)
        #print("Theta - 90 deg",theta_tilt)
        n = np.arange(self.n_rows) + 1
        m = np.arange(self.n_cols) + 1

        exp_arg = (n[:, np.newaxis] - 1) * self.dv * np.sin(r_theta) - \
                  (m - 1) * self.dh * np.cos(r_theta) * np.sin(r_phi)

        w_vec = (1 / np.sqrt(self.n_rows * self.n_cols)) *\
            np.exp(2 * np.pi * 1.0j * exp_arg)
        #print("w_vec from function: ", w_vec)
        return w_vec
    
    #Subarray gain

    def _calculate_subarray_gain(self, theta: float) -> float:
        """
        Calculates the subarray gain for a given theta.

        Parameters
        ----------
        theta : float
        Angle in degrees for which the subarray gain is calculated.

        Returns
        -------
        array_sub : float
        Subarray gain [dBi].
        """
    # Declarar parámetros fijos
        n_s_rows = 3
        dv_sub = 0.7
        theta_sub_tilt = 3  # Ángulo de tilt en grados
    
        # Convertir ángulos a radianes
        r_theta_3 = np.deg2rad(theta_sub_tilt)
        r_theta = np.deg2rad(theta)
        #print("theta:", theta, "r_theta:", r_theta)
        #print("theta_sub_tilt:", theta_sub_tilt, "r_theta_3:", r_theta_3)

        # Crear índices del subarreglo
        n_s = np.arange(n_s_rows) + 1
        #print("n_s:", n_s)

        # Calcular w_sub
        exp_arg_s = (n_s[:, np.newaxis] - 1) * dv_sub * np.sin(r_theta_3)
        w_sub = (1 / np.sqrt(n_s_rows)) * np.exp(2 * np.pi * 1.0j * exp_arg_s)
        #print("w_sub:", w_sub)

        # Calcular v_sub
        v_sub = np.exp(2 * np.pi * 1.0j * (n_s[:, np.newaxis] - 1) * dv_sub * np.cos(r_theta))
        #print("v_sub:", v_sub)

        # Multiplicación y suma
        multi_p = np.multiply(v_sub, w_sub)
        #print("multi_p:", multi_p)

        sumi_p = np.sum(multi_p)
        #print("sumi_p:", sumi_p)

        # Calcular ganancia del subarreglo
        array_sub = 10 * np.log10(abs(sumi_p) ** 2)
        #print("array_sub:", array_sub)

        return array_sub

    def _beam_gain(self, phi: float, theta: float, beam=-1,  station_type=None) -> float:
        """
        Calculates gain for a single beam in a given direction.
        Angles are in the local coordinate system.

        Parameters
        ----------
            phi (float): azimuth angle [degrees]
            theta (float): elevation angle [degrees]
            beam (int): Optional, beam index. If not provided, maximum gain is
                calculated

        Returns
        -------
            gain (float): beam gain [dBi]
        """

        element_g = self.element.element_pattern(phi, theta)
        #print(f"Tipo de estación en _beam_gain: {station_type}")  # Para verificar el valor
        v_vec = self._super_position_vector(phi, theta)
        #print("beam: ",beam)

        if(station_type == StationType.IMT_BS):
            sub_array_vec = self._calculate_subarray_gain(theta)
            #sub_array_vec = 0
        else:
            sub_array_vec = 0
        #print("Ganancia del subarreglo", sub_array_vec)

        if beam == -1:
            #print("Vector V Beam = -1: ", v_vec)
            #print("W vector: Beam = -1: ", self.w_vec_list[beam])
            w_vec = self._weight_vector(phi, theta - 90)
            array_g = 10 * np.log10(abs(np.sum(np.multiply(v_vec, w_vec)))**2)
        else:
            #print("Vector V: ", v_vec)
            #print("W vector: ", self.w_vec_list[beam])
            array_g = 10 * np.log10(
                abs(
                    np.sum(
                        np.multiply(
                            v_vec,
                            self.w_vec_list[beam],
                        ),
                    ),
                )**2,
            )
        #print("array g: ",array_g)
        #print("element g: ", element_g)
        #print("subarray g: ",sub_array_vec)
        

        gain = sub_array_vec+ element_g + array_g
        #print("ganancia Total del arreglo", gain)
        return gain

    def to_local_coord(self, phi: float, theta: float) -> tuple:
        """Returns phi and theta to antennas local coordintate system

        Parameters
        ----------
        phi : float
            phi in the simulator's coordinate system
        theta : float
            theta in the simulator's coordinate system

        Returns
        -------
        tuple
            phi, theta in the antenna's coordinate system
        """

        phi_rad = np.ravel(np.array([np.deg2rad(phi)]))
        theta_rad = np.ravel(np.array([np.deg2rad(theta)]))

        points = np.matrix([
            np.sin(theta_rad) * np.cos(phi_rad),
            np.sin(theta_rad) * np.sin(phi_rad),
            np.cos(theta_rad),
        ])

        rotated_points = self.rotation_mtx * points

        lo_phi = np.ravel(
            np.asarray(
                np.rad2deg(
                    np.arctan2(rotated_points[1], rotated_points[0]),
                ),
            ),
        )
        lo_theta = np.ravel(
            np.asarray(
                np.rad2deg(np.arccos(rotated_points[2])),
            ),
        )

        return lo_phi, lo_theta

    def _calculate_rotation_matrix(self):

        alpha = np.deg2rad(self.azimuth)
        beta = np.deg2rad(self.elevation)

        ry = np.matrix([
            [np.cos(beta), 0.0, np.sin(beta)],
            [0.0, 1.0, 0.0],
            [-np.sin(beta), 0.0, np.cos(beta)],
        ])
        rz = np.matrix([
            [np.cos(alpha), -np.sin(alpha), 0.0],
            [np.sin(alpha), np.cos(alpha), 0.0],
            [0.0, 0.0, 1.0],
        ])
        self.rotation_mtx = ry * np.transpose(rz)

###############################################################################


class PlotAntennaPattern(object):
    """
    Plots imt antenna pattern.
    """

    def __init__(self, figs_dir):
        self.figs_dir = figs_dir

    def plot_element_pattern(
        self,
        antenna: AntennaBeamformingImt,
        sta_type: str,
        plot_type: str,
    ):
        #print("sta_type: ", sta_type)
        phi_escan = 0
        theta_tilt = 90

        if sta_type == "BS":
            station_type = StationType.IMT_BS  # Asignamos el valor del enum
        elif sta_type == "UE":
            station_type = StationType.IMT_UE  # Asignamos el valor del enum
        elif sta_type == "TX":
            # Agrega la lógica necesaria para "TX"
            station_type = StationType.IMT_BS
        else:
            raise ValueError(f"Unknown station type: {sta_type}")  # Para otros casos no válidos
    
        #print("Station_Type: ", station_type)
        # Plot horizontal pattern
        phi = np.linspace(-180, 180, num=360) # le movi yo original 360
        theta = theta_tilt * np.ones(np.size(phi))
        gain = np.ones(phi.shape, dtype=float) * -500

        if plot_type == "ELEMENT":
            gain = antenna.element.element_pattern(phi, theta)
        elif plot_type == "ARRAY":
            antenna.add_beam(phi_escan, theta_tilt)
            gain = antenna.calculate_gain(
                phi_vec=phi,
                theta_vec=theta,
                beams_l=np.zeros_like(phi, dtype=int),station_type=station_type)

        top_y_lim = np.ceil(np.max(gain) / 10) * 10

        fig = plt.figure(figsize=(15, 5), facecolor='w', edgecolor='k')
        ax1 = fig.add_subplot(121)

        ax1.plot(phi, gain)
        ax1.grid(True)
        ax1.set_xlabel(r"$\varphi$ [deg]")
        ax1.set_ylabel("Gain [dBi]")

        if plot_type == "ELEMENT":
            ax1.set_title(
                "IMT " + sta_type +
                " element horizontal antenna pattern",
            )
        elif plot_type == "ARRAY":
            ax1.set_title("IMT " + sta_type + " horizontal antenna pattern")

        ax1.set_xlim(-180, 180)

        # Plot vertical pattern
        theta = np.linspace(0, 180, num=360)
        phi = phi_escan * np.ones(np.size(theta))

        if plot_type == "ELEMENT":
            gain = antenna.element.element_pattern(phi, theta)
        elif plot_type == "ARRAY":
            gain = antenna.calculate_gain(
                phi_vec=phi,
                theta_vec=theta,
                beams_l=np.zeros_like(phi, dtype=int),station_type=station_type
            )

        ax2 = fig.add_subplot(122, sharey=ax1)

        ax2.plot(theta, gain)
        ax2.grid(True)
        ax2.set_xlabel(r"$\theta$ [deg]")
        ax2.set_ylabel("Gain [dBi]")

        if plot_type == "ELEMENT":
            ax2.set_title(
                "IMT " + sta_type +
                " element vertical antenna pattern",
            )
        elif plot_type == "ARRAY":
            ax2.set_title("IMT " + sta_type + " vertical antenna pattern")

        ax2.set_xlim(0, 180)
        if np.max(gain) > top_y_lim:
            top_y_lim = np.ceil(np.max(gain) / 10) * 10
        ax2.set_ylim(top_y_lim - 60, top_y_lim)

        if sta_type == "BS":
            file_name = self.figs_dir + "bs_"
        else:  # sta_type == "UE":
            file_name = self.figs_dir + "ue_"

        if plot_type == "ELEMENT":
            file_name = file_name + "element_pattern.png"
        elif plot_type == "ARRAY":
            file_name = file_name + "array_pattern.png"

        # plt.savefig(file_name)
        plt.show()
        return fig


if __name__ == '__main__':

    figs_dir = "figs/"

    bs_param = ParametersAntennaImt()
    ue_param = ParametersAntennaImt()
    bs_param.adjacent_antenna_model = "BEAMFORMING"
    ue_param.adjacent_antenna_model = "BEAMFORMING"
    bs_param.normalization = True
    ue_param.normalization = False
    bs_param.normalization_file = "c:/python/SHARC-development_updated/SHARC-development/sharc/antenna/beamforming_normalization/bs_norm_8x16_050.npz"
    ue_param.normalization_file = "c:/python/SHARC-development_updated/SHARC-development/sharc/antenna/beamforming_normalization/ue_norm_1x1_050.npz"
    bs_param.minimum_array_gain = -200
    ue_param.minimum_array_gain = -200

    bs_param.element_pattern = "M2101"
    bs_param.element_max_g = 6.4
    bs_param.element_phi_3db = 90
    bs_param.element_theta_3db = 65
    bs_param.element_am = 30
    bs_param.element_sla_v = 30
    bs_param.n_rows = 8
    bs_param.n_columns = 16
    bs_param.element_horiz_spacing = 0.5
    bs_param.element_vert_spacing = 2.1
    bs_param.multiplication_factor = 12
    bs_param.downtilt = 6

    ue_param.element_pattern = "FIXED"
    ue_param.element_max_g = -4
    ue_param.element_phi_3db = 90
    ue_param.element_theta_3db = 65
    ue_param.element_am = 30
    ue_param.element_sla_v = 30
    ue_param.n_rows = 1
    ue_param.n_columns = 1
    ue_param.element_horiz_spacing = 0.5
    ue_param.element_vert_spacing = 0.5
    ue_param.multiplication_factor = 12

    plot = PlotAntennaPattern(figs_dir)

    # Plot BS TX radiation patterns
    par = bs_param.get_antenna_parameters()
    bs_array = AntennaBeamformingImt(par, 0, 0)
    f = plot.plot_element_pattern(bs_array, "BS", "ELEMENT")
    # f.savefig(figs_dir + "BS_element.pdf", bbox_inches='tight')
    f = plot.plot_element_pattern(bs_array, "TX", "ARRAY")
    # f.savefig(figs_dir + "BS_array.pdf", bbox_inches='tight')

    # Plot UE TX radiation patterns
    par = ue_param.get_antenna_parameters()
    ue_array = AntennaBeamformingImt(par, 0, 0)
    plot.plot_element_pattern(ue_array, "UE", "ELEMENT")
    plot.plot_element_pattern(ue_array, "UE", "ARRAY")

    print('END')
