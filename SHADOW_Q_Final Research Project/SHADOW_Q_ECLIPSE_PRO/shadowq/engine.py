"""Qiskit-native fragment execution and observable reconstruction.

Exact operator-Schmidt decomposition of interfragment RXX/RZZ gates.
Finite-shot local transition amplitudes use Hadamard tests on each fragment;
no full-width circuit is run during cut reconstruction.
"""
from dataclasses import dataclass
from functools import reduce
import numpy as np
from qiskit import QuantumCircuit, transpile
from qiskit.quantum_info import Statevector, SparsePauliOp
from qiskit_aer import AerSimulator

@dataclass(frozen=True)
class Config:
    n:int=6
    width:int=2
    layers:int=2
    angle:float=0.4
    seed:int=7
    J:float=1.0
    field:float=0.3

def operations(cfg):
    rng=np.random.default_rng(cfg.seed)
    ops=[]
    for layer in range(cfg.layers):
        for q in range(cfg.n):
            ops += [('ry',q,float(rng.uniform(-0.85,0.85))),('rz',q,float(rng.uniform(-0.5,0.5)))]
        for q in range(layer%2,cfg.n-1,2):ops.append(('rxx',q,cfg.angle))
        for q in range((layer+1)%2,cfg.n-1,2):ops.append(('rzz',q,cfg.angle))
    return ops

def circuit(cfg):
    qc=QuantumCircuit(cfg.n)
    for op,q,theta in operations(cfg):getattr(qc,op)(theta,q,q+1) if op in ('rxx','rzz') else getattr(qc,op)(theta,q)
    return qc

def groups_fixed(cfg):return [list(range(i,min(i+cfg.width,cfg.n))) for i in range(0,cfg.n,cfg.width)]

def groups_adaptive(cfg):
    """Observable-independent local interaction proxy; does not inspect full state.
    Minimize crossing operator-Schmidt rank and number of fragments.
    """
    count=np.zeros(cfg.n-1)
    for op,q,theta in operations(cfg):
        if op in ('rxx','rzz'):count[q]+=abs(np.sin(theta/2))
    dp=[(float('inf'),None)]*(cfg.n+1);dp[0]=(0.,None)
    for end in range(1,cfg.n+1):
        for start in range(max(0,end-cfg.width),end):
            score=dp[start][0]+0.12+(float(count[start-1]) if start else 0)
            if score<dp[end][0]:dp[end]=(score,start)
    out=[];end=cfg.n
    while end:
        start=dp[end][1];out.append(list(range(start,end)));end=start
    return out[::-1]

def branch_circuits(cfg,groups):
    owner={q:i for i,g in enumerate(groups) for q in g}
    branches=[(1.+0j,[QuantumCircuit(len(g)) for g in groups])]
    cuts=0
    for op,q,theta in operations(cfg):
        if op in ('ry','rz'):
            for amp,frags in branches:getattr(frags[owner[q]],op)(theta,q-groups[owner[q]][0])
        elif owner[q]==owner[q+1]:
            for amp,frags in branches:getattr(frags[owner[q]],op)(theta,q-groups[owner[q]][0],q+1-groups[owner[q]][0])
        else:
            cuts+=1; nxt=[]
            for amp,frags in branches:
                nxt.append((amp*np.cos(theta/2),[f.copy() for f in frags]))
                modified=[f.copy() for f in frags]
                pauli='x' if op=='rxx' else 'z'
                getattr(modified[owner[q]],pauli)(q-groups[owner[q]][0]);getattr(modified[owner[q+1]],pauli)(q+1-groups[owner[q+1]][0])
                nxt.append((-1j*amp*np.sin(theta/2),modified))
            branches=nxt
    return branches,cuts

def terms(cfg):
    out=[]
    for q in range(cfg.n-1):
        for p in 'XYZ':
            s=['I']*cfg.n;s[q]=s[q+1]=p;out.append((''.join(s),cfg.J,f'{p}{q}{p}{q+1}'))
    for q in range(cfg.n):
        s=['I']*cfg.n;s[q]='Z';out.append((''.join(s),cfg.field,f'Z{q}'))
    return out

def full_expectation(cfg,pauli):
    # Qiskit Pauli labels are little-endian; leftmost is highest-index qubit.
    return float(np.real(Statevector.from_instruction(circuit(cfg)).expectation_value(SparsePauliOp(pauli[::-1]))))

def overlap_exact(left,right,pauli):
    a=Statevector.from_instruction(left).data;b=Statevector.from_instruction(right).data
    label=pauli[::-1]
    p=SparsePauliOp(label).to_matrix()
    return complex(np.vdot(a,p@b))

def hadamard_circuit(left,right,pauli,imag=False):
    """Estimate <left|P|right> using ancilla coherence; fragment+one ancilla.
    Branch 0 prepares left; branch 1 prepares right and then applies P.
    Measuring X gives real overlap, Y gives imaginary overlap.
    """
    n=left.num_qubits; qc=QuantumCircuit(n+1,1);a=n
    qc.h(a)
    qc.append(left.to_gate().control(1,ctrl_state='0'),[a]+list(range(n)))
    qc.append(right.to_gate().control(1),[a]+list(range(n)))
    # P must be controlled on the |1> branch.
    for q,p in enumerate(pauli):
        if p!='I':getattr(qc,'c'+p.lower())(a,q)
    if imag:qc.sdg(a)
    qc.h(a);qc.measure(a,0)
    return qc

def estimate_overlap(left,right,pauli,shots=2048,seed=1,backend=None):
    backend=backend or AerSimulator()
    circuits=[hadamard_circuit(left,right,pauli,False),hadamard_circuit(left,right,pauli,True)]
    result=backend.run(transpile(circuits,backend,optimization_level=0),shots=shots,seed_simulator=seed).result()
    means=[];vars=[]
    for i in range(2):
        counts=result.get_counts(i);m=(counts.get('0',0)-counts.get('1',0))/shots
        means.append(m);vars.append(max(0.,1-m*m)/shots)
    return complex(*means),float(sum(vars))

def exact_reconstruct(cfg,groups,selected_terms=None):
    branches,cuts=branch_circuits(cfg,groups)
    if len(branches)>128:raise ValueError('Too many branches for interactive exact mode; reduce layers or increase fragment width')
    selected_terms=selected_terms or terms(cfg)
    results={}; cache={}
    for pauli,w,name in selected_terms:
        total=0j
        for i,(ai,fi) in enumerate(branches):
            for j,(aj,fj) in enumerate(branches):
                v=ai.conjugate()*aj
                for idx,g in enumerate(groups):
                    key=(i,j,idx,''.join(pauli[q] for q in g))
                    if key not in cache:cache[key]=overlap_exact(fi[idx],fj[idx],key[-1])
                    v*=cache[key]
                total+=v
        results[name]={'reconstructed':float(total.real),'full':full_expectation(cfg,pauli),'weight':w}
    return {'groups':groups,'cuts':cuts,'branches':len(branches),'results':results,
            'energy_reconstructed':sum(v['reconstructed']*v['weight'] for v in results.values()),
            'energy_full':sum(v['full']*v['weight'] for v in results.values())}

def sampled_reconstruct(cfg,groups,selected_terms=None,shots_per_overlap=512,seed=11,max_experiments=3000):
    """Hardware-measurable local Hadamard tests; each execution <= width+1 qubits.
    This is a genuine finite-shot circuit-cut reconstruction baseline, NOT classical shadows.
    """
    branches,cuts=branch_circuits(cfg,groups)
    selected_terms=selected_terms or terms(cfg)
    required=len(branches)**2*len(groups)*len(selected_terms)
    if required>max_experiments:raise ValueError(f'{required} overlap pairs; reduce layers or terms')
    backend=AerSimulator();cache={};out={}
    for pauli,w,name in selected_terms:
        total=0j
        for i,(ai,fi) in enumerate(branches):
            for j,(aj,fj) in enumerate(branches):
                v=ai.conjugate()*aj
                for idx,g in enumerate(groups):
                    local=''.join(pauli[q] for q in g);key=(i,j,idx,local)
                    if key not in cache:
                        cache[key]=estimate_overlap(fi[idx],fj[idx],local,shots_per_overlap,seed+len(cache),backend)
                    v*=cache[key][0]
                total+=v
        out[name]={'sampled':float(total.real),'exact':full_expectation(cfg,pauli),'weight':w}
    return {'groups':groups,'cuts':cuts,'branches':len(branches),'results':out,
            'energy_sampled':sum(v['sampled']*v['weight'] for v in out.values()),
            'energy_full':sum(v['exact']*v['weight'] for v in out.values()),
            'unique_overlap_circuits':2*len(cache),'total_shots':2*len(cache)*shots_per_overlap,
            'maximum_executed_qubits':max(map(len,groups))+1,
            'method':'finite-shot local Hadamard-test reconstruction (not classical shadows)'}
