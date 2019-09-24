import mxnet as mx
import numpy as np
from matplotlib import pyplot
import tempfile
try:
    import graphviz
except ImportError:
    graphviz = None

__all__ = ['Visualizer', 'plot_network']

def plot_network(block, shape=(1, 3, 224, 224), savefile=False):
    """Plot network to visualize internal structures.

    Parameters
    ----------
    block : mxnet.gluon.HybridBlock
        A hybridizable network to be visualized.
    shape : tuple of int
        Desired input shape, default is (1, 3, 224, 224).
    save_prefix : str or None
        If not `None`, will save rendered pdf to disk with prefix.

    """
    if graphviz is None:
        raise RuntimeError("Cannot import graphviz.")
    if not isinstance(block, mx.gluon.HybridBlock):
        raise ValueError("block must be HybridBlock, given {}".format(type(block)))
    data = mx.sym.var('data')
    sym = block(data)
    if isinstance(sym, tuple):
        sym = mx.sym.Group(sym)

    a = mx.viz.plot_network(sym, shape={'data':shape},
                            node_attrs={'shape':'rect', 'fixedsize':'false'})
    if savefile:
        a.view(tempfile.mktemp('.gv'))
    #if isinstance(save_prefix, str):
    #    a.render(save_prefix)
    return a

class Visualizer(object):
    def __init__(self):
        pass

    @staticmethod
    def visualize_dataset_label_histogram(a, b):
        min_len = min(len(a._label), len(b._label))
        pyplot.hist([a._label[:min_len], b._label[:min_len]],
                    bins=len(np.unique(a._label)),
                    label=['a', 'b'])
        pyplot.legend(loc='upper right')
        pyplot.savefig('./histogram.png')
