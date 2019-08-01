import math
import itertools as itt
import numpy as np
from collections import namedtuple
from datetime import datetime
from scipy.special import gamma
from sklearn.neighbors import BallTree

from pywde.pywt_ext import WaveletTensorProduct
from pywde.common import all_zs_tensor


class SPWDE(object):
    def __init__(self, waves, k=1):
        self.wave = WaveletTensorProduct([wave_desc[0] for wave_desc in waves])
        self.j0s = [wave_desc[1] for wave_desc in waves]
        self.k = k
        self.minx = None
        self.maxx = None

    MODE_NORMED = 'normed'
    MODE_DIFF = 'diff'

    def best_j(self, xs, mode):
        t0 = datetime.now()
        if mode not in [self.MODE_NORMED, self.MODE_DIFF]:
            raise ValueError('Mode is wrong')
        best_j_data = []
        balls_info = calc_sqrt_vs(xs, self.k)
        self.minx = np.amin(xs, axis=0)
        self.maxx = np.amax(xs, axis=0)
        for j in range(7):
            # In practice, one would stop when maximum is reached, i.e. after first decreasing value of B Hat
            tots = []
            base_fun, base_fun_xs, dual_fun_xs = self.calc_funs_at(j, xs, (0, 0))
            if mode == self.MODE_DIFF:
                alphas_dict = self.calc_coeffs(base_fun_xs, dual_fun_xs, j, xs, balls_info, (0, 0))
                alphas2 = np.array(list(alphas_dict.values()))
                alphas2 = (alphas2[:,0] * alphas2[:,1]).sum()
            for i, x in enumerate(xs):
                alphas = self.calc_coeffs_no_i(base_fun_xs, dual_fun_xs, j, xs, i, balls_info, (0, 0))
                g_ring_x = 0.0
                norm2 = 0.0
                for zs in alphas:
                    if zs not in base_fun_xs:
                        continue
                    alpha_zs, alpha_d_zs = alphas[zs]
                    g_ring_x += alpha_zs * base_fun_xs[zs][i]
                    norm2 += alpha_zs * alpha_d_zs
                # q_ring_x ^ 2 / norm2 == f_at_x
                if norm2 == 0.0:
                    if g_ring_x == 0.0:
                        tots.append(0.0)
                    else:
                        raise RuntimeError('Got norms but no value')
                else:
                    if mode == self.MODE_NORMED:
                        tots.append(g_ring_x * g_ring_x / norm2)
                    else: # mode == self.MODE_DIFF:
                        tots.append(g_ring_x * g_ring_x)
            tots = np.array(tots)
            if mode == self.MODE_NORMED:
                b_hat_j = calc_omega(xs.shape[0], self.k) * (np.sqrt(tots) * balls_info.sqrt_vol_k).sum()
            else: # mode == self.MODE_DIFF:
                b_hat_j = 2 * calc_omega(xs.shape[0], self.k) * (np.sqrt(tots) * balls_info.sqrt_vol_k).sum() - alphas2
            print(j, b_hat_j)
            # if calculating pdf
            name = 'WDE Alphas, dj=%d' % j
            if mode == self.MODE_DIFF:
                pdf = self.calc_pdf(base_fun, alphas_dict, name)
            else:
                alphas_dict = self.calc_coeffs(base_fun_xs, dual_fun_xs, j, xs, balls_info, (0, 0))
                pdf = self.calc_pdf(base_fun, alphas_dict, name)
            elapsed = (datetime.now() - t0).total_seconds()
            best_j_data.append((j, b_hat_j, pdf, elapsed))
        best_b_hat = max([info_j[1] for info_j in best_j_data])
        best_j = list(filter(lambda info_j: info_j[1] == best_b_hat, best_j_data))[0][0]
        self.best_j_data = [
            tuple([info_j[0], info_j[0] == best_j, info_j[1], info_j[2], info_j[3]])
            for info_j in best_j_data]


    def best_c(self, xs, delta_j):
        "best c - hard thresholding"
        assert delta_j > 0, 'delta_j must be 1 or more'
        balls_info = calc_sqrt_vs(xs, self.k)
        self.minx = np.amin(xs, axis=0)
        self.maxx = np.amax(xs, axis=0)
        qqs = self.wave.qq

        # base funs for levels of interest
        base_funs_j = {}
        base_funs_j[(0, qqs[0])] = self.calc_funs_at(0, xs, qqs[0])
        for j, qq in itt.product(range(delta_j), qqs[1:]):
            base_funs_j[(j, qq)] = self.calc_funs_at(j, xs, qq)
        # base_funs_j [ (j, qq) ] => base_fun, base_fun_xs, dual_fun_xs

        # rank betas from large to smallest; we will incrementaly calculate
        # the HD_i for each in turn
        all_betas = []
        for j, qq in base_funs_j:
            base_fun, base_fun_xs, dual_fun_xs = base_funs_j[(j, qq)]
            if qq == (0, 0):
                continue
            cc = self.calc_coeffs(base_fun_xs, dual_fun_xs, j, xs, balls_info, qq)
            for zs in cc:
                coeff_zs, coeff_d_zs = cc[zs]
                if coeff_zs == 0.0:
                    continue
                all_betas.append((qq, j, zs, coeff_zs, coeff_d_zs))
        # bio is sqrt( beta * dual_beta ) ??
        key_order = lambda tt: math.fabs(tt[3])/math.sqrt(tt[1]+1)
        all_betas = sorted(all_betas, key=key_order, reverse=True)

        # get base line for acummulated values by computing alphas and the
        # target HD_i functions
        base_fun, base_fun_xs, dual_fun_xs = base_funs_j[(0, (0, 0))]
        cc = self.calc_coeffs(base_fun_xs, dual_fun_xs, 0, xs, balls_info, (0, 0))
        norm2 = 0.0
        vs_i = np.zeros(xs.shape[0])
        for zs in cc:
            coeff_zs, coeff_d_zs = cc[zs]
            vs_i += coeff_zs * base_fun_xs[zs]
            norm2 += coeff_zs * coeff_d_zs

        # iterate through betas (similar to how we iterated through J in best_j)
        all_balls = []
        for i in range(len(xs)):
            balls = balls_no_i(balls_info, i)
            all_balls.append(balls)

        omega_nk = calc_omega(xs.shape[0], self.k)
        best_c_data = []
        q_norm2 = norm2
        for beta_info in all_betas:
            qq, j, zs, coeff , coeff_d = beta_info
            q_norm2 += coeff * coeff_d
            for i, x in enumerate(xs):
                base_fun, base_fun_xs, dual_fun_xs = base_funs_j[(j, qq)]
                coeff_i, coeff_d_i = self.calc_1_coeff_no_i(base_fun_xs, dual_fun_xs, j, xs, i, all_balls[i], qq, zs)
                vs_i[i] += coeff_i * base_fun_xs[zs][i]
            b_hat_beta = 2 * omega_nk * (np.sqrt(vs_i * vs_i) * balls_info.sqrt_vol_k).sum() - q_norm2
            best_c_data.append((key_order(beta_info), b_hat_beta))
        self.best_c_data = best_c_data

    def calc_pdf(self, base_fun, alphas, name):
        norm2 = 0.0
        for zs in alphas:
            if zs not in base_fun:
                continue
            alpha_zs, alpha_d_zs = alphas[zs]
            norm2 += alpha_zs * alpha_d_zs
        if norm2 == 0.0:
            raise RuntimeError('No norm')

        def pdf(xs, alphas=alphas, norm2=norm2, base_fun=base_fun):
            g_ring_xs = np.zeros(xs.shape[0])
            for zs in alphas:
                if zs not in base_fun:
                    continue
                alpha_zs, alpha_d_zs = alphas[zs]
                g_ring_xs += alpha_zs * base_fun[zs](xs)
            # q_ring_x ^ 2 / norm2 == f_at_x
            return g_ring_xs * g_ring_xs / norm2
        pdf.name = name
        return pdf

    def calc_funs_at(self, j, xs, qq):
        base_fun, dual_fun = self.calc_funs(j, qq)
        base_fun_xs = {}
        for zs in base_fun:
            base_fun_xs[zs] = base_fun[zs](xs)
        dual_fun_xs = {}
        for zs in dual_fun:
            dual_fun_xs[zs] = dual_fun[zs](xs)
        return base_fun, base_fun_xs, dual_fun_xs

    def calc_funs(self, j, qq):
        jj = [j + j0 for j0 in self.j0s]
        jpow2 = np.array([2 ** j for j in jj])

        funs = {}
        for what in ['dual', 'base']:
            zs_min, zs_max = self.wave.z_range(what, (qq, jpow2, None), self.minx, self.maxx)
            funs[what] = {}
            for zs in itt.product(*all_zs_tensor(zs_min, zs_max)):
                funs[what][zs] = self.wave.fun_ix(what, (qq, jpow2, zs))
        return funs['base'], funs['dual']

    def calc_coeffs_no_i(self, base_fun_xs, dual_fun_xs, j, xs, i, balls_info, qq):
        "Calculate alphas (w/ dual) and alpha-duals (w/ base)"
        jj = [j + j0 for j0 in self.j0s]
        jpow2 = np.array([2 ** j for j in jj])
        zs_min, zs_max = self.wave.z_range('dual', (qq, jpow2, None), self.minx, self.maxx)
        omega_no_i = calc_omega(xs.shape[0] - 1, self.k)
        resp = {}
        balls = balls_no_i(balls_info, i)
        for zs in itt.product(*all_zs_tensor(zs_min, zs_max)):
            # below, we remove factor for i from sum << this has the biggest impact in performance
            alpha_zs = omega_no_i * ((dual_fun_xs[zs] * balls).sum() - dual_fun_xs[zs][i] * balls[i])
            resp[zs] = (alpha_zs, alpha_zs)
        if self.wave.orthogonal:
            # we are done
            return resp
        zs_min, zs_max = self.wave.z_range('base', (qq, jpow2, None), self.minx, self.maxx)
        for zs in itt.product(*all_zs_tensor(zs_min, zs_max)):
            if zs not in resp:
                continue
            # below, we remove factor for i from sum << this has the biggest impact in performance
            alpha_d_zs = omega_no_i * ((base_fun_xs[zs] * balls).sum() - base_fun_xs[zs][i] * balls[i])
            resp[zs] = (resp[zs][0], alpha_d_zs)
        return resp

    def calc_1_coeff_no_i(self, base_fun_xs, dual_fun_xs, j, xs, i, balls, qq, zs):
        omega_no_i = calc_omega(xs.shape[0] - 1, self.k)
        coeff = omega_no_i * ((dual_fun_xs[zs] * balls).sum() - dual_fun_xs[zs][i] * balls[i])
        if self.wave.orthogonal:
            # we are done
            return coeff, coeff
        coeff_d = omega_no_i * ((base_fun_xs[zs] * balls).sum() - base_fun_xs[zs][i] * balls[i])
        return coeff, coeff_d

    def calc_coeffs(self, base_fun_xs, dual_fun_xs, j, xs, balls_info, qq):
        jj = [j + j0 for j0 in self.j0s]
        jpow2 = np.array([2 ** j for j in jj])
        zs_min, zs_max = self.wave.z_range('dual', (qq, jpow2, None), self.minx, self.maxx)
        omega = calc_omega(xs.shape[0], self.k)
        resp = {}
        balls = balls_info.sqrt_vol_k
        for zs in itt.product(*all_zs_tensor(zs_min, zs_max)):
            alpha_zs = omega * (dual_fun_xs[zs] * balls).sum()
            resp[zs] = (alpha_zs, alpha_zs)
        if self.wave.orthogonal:
            # we are done
            return resp
        zs_min, zs_max = self.wave.z_range('base', (qq, jpow2, None), self.minx, self.maxx)
        for zs in itt.product(*all_zs_tensor(zs_min, zs_max)):
            if zs not in resp:
                continue
            alpha_d_zs = omega * (base_fun_xs[zs] * balls).sum()
            resp[zs] = (resp[zs][0], alpha_d_zs)
        return resp


def balls_no_i(balls_info, i):
    n = balls_info.nn_indexes.shape[0]
    resp = []
    for i_prim in range(n):
        # note index i is removed at callers site
        if i in balls_info.nn_indexes[i_prim, :-1]:
            resp.append(balls_info.sqrt_vol_k_plus_1[i_prim])
        else:
            resp.append(balls_info.sqrt_vol_k[i_prim])
    return np.array(resp)


def calc_omega(n, k):
    "Bias correction for k-th nearest neighbours sum for sample size n"
    return math.sqrt(n - 1) * gamma(k) / gamma(k + 0.5) / n



BallsInfo = namedtuple('BallsInfo', ['sqrt_vol_k', 'sqrt_vol_k_plus_1', 'nn_indexes'])


def calc_sqrt_vs(xs, k):
    "Returns BallsInfo object with sqrt of volumes of k-th balls and (k+1)-th balls"
    dim = xs.shape[1]
    ball_tree = BallTree(xs)
    # as xs is both data and query, xs's nearest neighbour would be xs itself, hence the k+2 below
    dist, inx = ball_tree.query(xs, k + 2)
    k_near_radious = dist[:, -2:]
    xs_balls_both = np.power(k_near_radious, dim / 2)
    xs_balls = xs_balls_both[:, 0] * sqrt_vunit(dim)
    xs_balls2 = xs_balls_both[:, 1] * sqrt_vunit(dim)
    return BallsInfo(xs_balls, xs_balls2, inx)


def sqrt_vunit(dim):
    "Square root of Volume of unit hypersphere in d dimensions"
    return math.sqrt((np.pi ** (dim / 2)) / gamma(dim / 2 + 1))
