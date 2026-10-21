"""Adaptive shot allocation for physically executable fragment Hadamard tests.

The estimator is NOT full classical-shadow cutting: it estimates fragment
transition amplitudes via interferometry. No statevector oracle is used to
allocate shots; only pilot counts are used.
"""
import numpy as np
from qiskit_aer import AerSimulator
from qiskit import transpile
from .engine import branch_circuits, terms, full_expectation, hadamard_circuit


def reconstruct(cfg, groups, selected_terms=None, budget=12000, pilot=32, seed=17,
                adaptive=True, max_circuits=500):
    if budget <= 0 or pilot < 2:
        raise ValueError('budget must be positive and pilot >= 2')
    selected_terms = selected_terms if selected_terms is not None else terms(cfg)
    branches, cuts = branch_circuits(cfg, groups)
    keys = {}
    for pauli, weight, name in selected_terms:
        for i in range(len(branches)):
            for j in range(len(branches)):
                for f, g in enumerate(groups):
                    local = ''.join(pauli[q] for q in g)
                    key = (i, j, f, local)
                    keys[key] = None
    if len(keys)*2 > max_circuits:
        raise ValueError('Too many fragment experiments; reduce layers/observables')
    circuits=[]; lookup=[]
    for key in keys:
        i,j,f,local=key
        for imaginary in (False,True):
            circuits.append(hadamard_circuit(branches[i][1][f],branches[j][1][f],local,imaginary))
            lookup.append((key,imaginary))
    n=len(circuits)
    circuits=transpile(circuits,AerSimulator(),optimization_level=0)
    if budget < n*pilot:
        raise ValueError(f'Budget {budget} is below required pilot shots {n*pilot}')
    backend=AerSimulator()
    initial=backend.run(circuits,shots=pilot,seed_simulator=seed).result()
    means=np.empty(n); variances=np.empty(n)
    for t in range(n):
        c=initial.get_counts(t)
        m=(c.get('0',0)-c.get('1',0))/pilot
        means[t]=m; variances[t]=max(0.01,1-m*m)
    remaining=budget-n*pilot
    if adaptive:
        # Pilot-only allocation: Neyman weights for uncertain experiments.
        weights=np.sqrt(variances)
    else:
        weights=np.ones(n)
    raw=remaining*weights/weights.sum()
    extra=np.floor(raw).astype(int)
    for t in np.argsort(-(raw-extra))[:remaining-int(extra.sum())]:extra[t]+=1
    # Aer run groups by equal shots to avoid 1-job-per-circuit overhead.
    from collections import defaultdict
    buckets=defaultdict(list)
    for idx,k in enumerate(extra):
        if k: buckets[int(k)].append(idx)
    for k, indices in buckets.items():
        res=backend.run([circuits[t] for t in indices],shots=k,seed_simulator=seed+100+k).result()
        for pos,t in enumerate(indices):
            c=res.get_counts(pos); m=(c.get('0',0)-c.get('1',0))/k
            means[t]=(means[t]*pilot+m*k)/(pilot+k)
    overlaps={}
    for t,(key,imag) in enumerate(lookup):
        if key not in overlaps:overlaps[key]=[0.,0.]
        overlaps[key][int(imag)]=float(means[t])
    overlaps={k:complex(*v) for k,v in overlaps.items()}
    results={}
    for pauli,w,name in selected_terms:
        total=0j
        for i,(ai,fi) in enumerate(branches):
            for j,(aj,fj) in enumerate(branches):
                v=ai.conjugate()*aj
                for f,g in enumerate(groups):
                    v*=overlaps[(i,j,f,''.join(pauli[q] for q in g))]
                total+=v
        results[name]={'estimate':float(total.real),'reference':full_expectation(cfg,pauli),'weight':w}
    return {'method':'adaptive interferometric fragment reconstruction',
            'shadow_cutting':False,'groups':groups,'cuts':cuts,'branches':len(branches),
            'total_shots':int(budget),'pilot_shots_per_circuit':pilot,
            'unique_experiments':n,'adaptive':adaptive,
            'maximum_executed_qubits':max(map(len,groups))+1,
            'energy_estimate':sum(v['estimate']*v['weight'] for v in results.values()),
            'energy_reference':sum(v['reference']*v['weight'] for v in results.values()),
            'results':results}
