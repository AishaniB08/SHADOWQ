"""Actual randomized local Pauli classical shadows for full-width baseline.
Each shot randomly chooses X/Y/Z on every qubit and estimates Pauli strings.
Not used as the cut-fragment reconstruction algorithm.
"""
import numpy as np
from qiskit_aer import AerSimulator
from .engine import circuit

def estimate(cfg,paulis,shots=1000,seed=4):
    rng=np.random.default_rng(seed);base=circuit(cfg);backend=AerSimulator()
    circuits=[];bases=[]
    for _ in range(shots):
        axes=rng.integers(0,3,cfg.n);q=base.copy()
        for j,axis in enumerate(axes):
            if axis==0:q.h(j)
            elif axis==1:q.sdg(j);q.h(j)
        q.measure_all();circuits.append(q);bases.append(axes)
    result=backend.run(circuits,shots=1,seed_simulator=seed).result()
    samples={p:[] for p in paulis}
    for i,axes in enumerate(bases):
        bitstring=next(iter(result.get_counts(i)))[::-1]
        for p in paulis:
            support=[j for j,x in enumerate(p) if x!='I']
            if all('XYZ'[axes[j]]==p[j] for j in support):
                samples[p].append((3**len(support))*np.prod([1-2*int(bitstring[j]) for j in support]))
            else:samples[p].append(0.)
    return {p:{'estimate':float(np.mean(v)),'stderr':float(np.std(v,ddof=1)/np.sqrt(shots)) if shots>1 else None} for p,v in samples.items()}
