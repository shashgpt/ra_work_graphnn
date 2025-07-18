'''
This code is adapted from process steps on eICU of previous works (cited)
https://github.com/Google-Health/records-research/tree/master/graph-convolutional-transformer
'''

import pandas as pd
import csv
import tensorflow as tf
tf.compat.v1.enable_eager_execution()

import sys
import pickle
from sklearn import model_selection
import argparse
from datetime import datetime
import numpy as np
import os
from scipy.sparse import csr_matrix

#Suppressing warnings
import warnings
warnings.simplefilter(action='ignore', category=FutureWarning)

class EncounterInfo(object):
    def __init__(self, patient_id, encounter_id, labeled):
        self.patient_id = patient_id # 'SUBJECT_ID'
        self.encounter_id = encounter_id # 'HADM_ID'
        # self.encounter_timestamp = encounter_timestamp # 'ADMITTIME'
        self.labeled = labeled # 'HOSPITAL_EXPIRE_FLAG'
        self.dx_ids = [] # Diagnosis - 'NDC CODE' // 'ICD9_CODE'
        self.rx_ids = [] 
        self.labs = {} 
        self.physicals = []
        self.treatments = [] # Procedure - 'ICD9_CODE'
        self.labvalues = [] # Labevent - 'VALUENUM' -> lab_id + range indexing


def process_patient(infile, encounter_dict, label, min_length_of_stay=0):
#     inff = open(infile, 'r')
#     count = 0
#     patient_dict = {}
#     for line in csv.DictReader(inff):
#         if count % 100 == 0:
#             sys.stdout.write('%d\r' % count)
#             sys.stdout.flush()
#         patient_id = line['SUBJECT_ID']
#         encounter_id = line['HADM_ID']
#         encounter_timestamp = line['ADMITTIME']
#         if patient_id not in patient_dict:
#             patient_dict[patient_id] = []
#         patient_dict[patient_id].append((encounter_timestamp, encounter_id))
#         count += 1
#     inff.close()
#     print('')
#     print(len(patient_dict))
#     patient_dict_sorted = {}
#     for patient_id, time_enc_tuples in patient_dict.items():
#         patient_dict_sorted[patient_id] = sorted(time_enc_tuples)

    evaluation_label = ['EXPIRE_FLAG', 'cohort', 'Obesity', 'Non.Adherence',
                        'Developmental.Delay.Retardation', 'Advanced.Heart.Disease',
                        'Advanced.Lung.Disease',
                        'Schizophrenia.and.other.Psychiatric.Disorders', 'Alcohol.Abuse',
                        'Other.Substance.Abuse', 'Chronic.Pain.Fibromyalgia',
                        'Chronic.Neurological.Dystrophies', 'Advanced.Cancer', 'Depression',
                        'Dementia', 'Unsure']
    label_name = evaluation_label[label]
    
    patient2label = {}
    
    inff = open(infile, 'r') # "/root/IDL_Project/MIMIC3/patient.csv"
    count = 0
    for line in csv.DictReader(inff):
        if count % 10000 == 0:
            sys.stdout.write('%d\r' % count)
            sys.stdout.flush()
        patient_id = str(int(float(line['SUBJECT_ID'])))
        encounter_id = str(int(float(line['HADM_ID'])))
#         encounter_timestamp = datetime.strptime(line['ADMITTIME'], '%Y-%m-%d %H:%M:%S')
#         expired = line['EXPIRE_FLAG'] == "1"
        labeled = line[label_name] == "1"
#         if (datetime.strptime(line['DISCHTIME'], '%Y-%m-%d %H:%M:%S') - encounter_timestamp).days < min_length_of_stay:
#             continue

#         ei = EncounterInfo(patient_id, encounter_id, labeled)
#         if encounter_id in encounter_dict:
#             print('Duplicate encounter ID!!')
#             print(encounter_id)
#             sys.exit(0)
#         encounter_dict[encounter_id] = ei
        patient2label[patient_id] = labeled
        count += 1
    
    inff.close()
    print('')
    return patient2label
#     return encounter_dict


def process_diagnosis(infile, patient2label): #encounter_dict):
    inff = open(infile, 'r')
    count = 0
    missing_pid = 0
    encounter_dict = {}
    for line in csv.DictReader(inff):
        if count % 10000 == 0:
            sys.stdout.write('%d\r' % count)
            sys.stdout.flush()
        patient_id = str(int(float(line['SUBJECT_ID'])))
        encounter_id = str(int(float(line['HADM_ID'])))
        dx_id = line['NDC'].lower() # line['ICD9_CODE'].lower()
        if patient_id not in patient2label:
            missing_pid += 1
            continue
        if encounter_id not in encounter_dict:
            labeled = patient2label[patient_id]
            ei = EncounterInfo(patient_id, encounter_id, labeled)
            encounter_dict[encounter_id] = ei
        encounter_dict[encounter_id].dx_ids.append(dx_id)
        count += 1
    inff.close()
    print('')
    print('Diagnosis without Encounter ID: %d' % missing_pid)
    return encounter_dict


def process_treatment(infile, encounter_dict):
    inff = open(infile, 'r')
    count = 0
    missing_eid = 0
    for line in csv.DictReader(inff):
        if count % 10000 == 0:
            sys.stdout.write('%d\r' % count)
            sys.stdout.flush()
        encounter_id = str(int(float(line['HADM_ID'])))
        treatment_id = line['ICD9_CODE'].lower()
        if encounter_id not in encounter_dict:
            missing_eid += 1
            continue
        encounter_dict[encounter_id].treatments.append(treatment_id)
        count += 1
    inff.close()
    print('')
    print('Treatment without Encounter ID: %d' % missing_eid)
    return encounter_dict


def get_lab_mean_std(lab_file, train_ids):
    lab_data = pd.read_csv(lab_file)
    lab_data = lab_data[(lab_data['SUBJECT_ID'].astype('str') + ':' +
         lab_data['HADM_ID'].apply(lambda x: f'{x:.0f}')).isin(train_ids)]
    lab_data = lab_data[lab_data.CHART_VALUENUM.notna()]
    mean_std = lab_data.groupby('CHART_ITEMID').agg({'CHART_VALUENUM': ['mean', "std"]}).reset_index()
    mean_std = mean_std[mean_std.CHART_VALUENUM['mean'].notna() & mean_std.CHART_VALUENUM['std'].notna()]
    mean_std = dict(zip(np.array(mean_std['CHART_ITEMID']).astype('str'),
                        [(row['CHART_VALUENUM']['mean'], row['CHART_VALUENUM']['std'])
                         for _, row in mean_std.iterrows()]))
    return mean_std


def process_lab(infile, encounter_dict, mean_std):
    inff = open(infile, 'r')
    count = 0
    missing_eid = 0
    for line in csv.DictReader(inff):
        if count % 10000 == 0:
            sys.stdout.write('%d\r' % count)
            sys.stdout.flush()
        encounter_id = str(int(float(line['HADM_ID'])))
        if len(encounter_id) == 0:
            continue
        lab_id = line['CHART_ITEMID'].lower()
        # lab_time = datetime.strptime(line['CHARTTIME'], '%Y-%m-%d %H:%M:%S')
        if encounter_id not in encounter_dict:
            missing_eid += 1
            continue
        if lab_id in mean_std:
            try:
                lab_value = float(line['CHART_VALUENUM'])
            except:
                missing_eid += 1
                continue
            mean, std = mean_std[lab_id]
            suffix = "_(>10)"
            for lab_range in ['-10', '-3', '-1', '-0.5', '0.5', '1', '3', '10']:
                if lab_value < float(lab_range) * std + mean:
                    suffix = "_({})".format(lab_range)
                    break
            # admission_time = encounter_dict[encounter_id].encounter_timestamp
            # if (lab_time - admission_time).days < 1:
            encounter_dict[encounter_id].labvalues.append(lab_id + suffix)
        count += 1
    inff.close()
    print('')
    print('Lab without Encounter ID: %d' % missing_eid)
    return encounter_dict


def build_seqex(enc_dict,
                skip_duplicate=False,
                min_num_codes=1,
                max_num_codes=50,
                labflag=False):
    key_list = []
    seqex_list = []
    dx_str2int = {}
    treat_str2int = {}
    lab_str2int = {}
    num_cut = 0
    num_duplicate = 0
    count = 0
    num_dx_ids = 0
    num_treatments = 0
    num_labs = 0
    num_unique_dx_ids = 0
    num_unique_treatments = 0
    num_unique_labs = 0
    min_dx_cut = 0
    min_treatment_cut = 0
    min_lab_cut = 0
    max_dx_cut = 0
    max_treatment_cut = 0
    max_lab_cut = 0
    num_labeled = 0

    for _, enc in enc_dict.items():
#         if skip_duplicate:
#             if (len(enc.dx_ids) > len(set(enc.dx_ids)) or len(enc.treatments) > len(set(enc.treatments))):
#                 num_duplicate += 1
#                 continue

        # filtering patients with proper number of dx_ids, treatments and labvalues (1-50)
#         if len(set(enc.dx_ids)) < min_num_codes:
#             min_dx_cut += 1
#             continue

#         if len(set(enc.treatments)) < min_num_codes:
#             min_treatment_cut += 1
#             continue

#         if len(set(enc.dx_ids)) > max_num_codes:
#             max_dx_cut += 1
#             continue

#         if len(set(enc.treatments)) > max_num_codes:
#             max_treatment_cut += 1
#             continue

#         if labflag:
#             if len(set(enc.labvalues)) < min_num_codes:
#                 min_lab_cut += 1
#                 continue
#             if len(set(enc.labvalues)) > max_num_codes:
#                 max_lab_cut += 1
#                 continue

        count += 1
        num_dx_ids += len(enc.dx_ids)
        num_treatments += len(enc.treatments)
        num_unique_dx_ids += len(set(enc.dx_ids))
        num_unique_treatments += len(set(enc.treatments))
        
        if labflag:
            num_labs += len(enc.labvalues)
            num_unique_labs += len(set(enc.labvalues))

        for dx_id in enc.dx_ids:
            if dx_id not in dx_str2int:
                dx_str2int[dx_id] = len(dx_str2int)

        for treat_id in enc.treatments:
            if treat_id not in treat_str2int:
                treat_str2int[treat_id] = len(treat_str2int)

        if labflag:
            for lab_id in enc.labvalues:
                if lab_id not in lab_str2int:
                    lab_str2int[lab_id] = len(lab_str2int)

        seqex = tf.train.SequenceExample()
        '''
        seqex_list
        patientId - Subject ID 
        label - labeled?
        dx_id -> original: dx_ids / str2int: dx_ints
        treatments -> original: proc_ids / str2int: proc_ints
        labvalues -> original: lab_ids / str2int: lab_ints
        '''
        seqex.context.feature['patientId'].bytes_list.value.append(bytes(enc.patient_id + ':' +
                                                                         enc.encounter_id, 'utf-8'))
#         seqex.context.feature['patientId'].bytes_list.value.append(bytes(enc.encounter_id, 'utf-8'))
        if enc.labeled:
            seqex.context.feature['label'].int64_list.value.append(1)
            num_labeled += 1
        else:
            seqex.context.feature['label'].int64_list.value.append(0)

        dx_ids = seqex.feature_lists.feature_list['dx_ids']
        dx_ids.feature.add().bytes_list.value.extend(list([bytes(s, 'utf-8') for s in set(enc.dx_ids)]))

        dx_int_list = [dx_str2int[item] for item in list(set(enc.dx_ids))]
        dx_ints = seqex.feature_lists.feature_list['dx_ints']
        dx_ints.feature.add().int64_list.value.extend(dx_int_list)

        proc_ids = seqex.feature_lists.feature_list['proc_ids']
        proc_ids.feature.add().bytes_list.value.extend(list([bytes(s, 'utf-8') for s in set(enc.treatments)]))

        proc_int_list = [treat_str2int[item] for item in list(set(enc.treatments))]
        proc_ints = seqex.feature_lists.feature_list['proc_ints']
        proc_ints.feature.add().int64_list.value.extend(proc_int_list)

        if labflag:
            lab_ids = seqex.feature_lists.feature_list['lab_ids']
            lab_ids.feature.add().bytes_list.value.extend(list([bytes(s, 'utf-8') for s in set(enc.labvalues)]))

            lab_int_list = [lab_str2int[item] for item in list(set(enc.labvalues))]
            lab_ints = seqex.feature_lists.feature_list['lab_ints']
            lab_ints.feature.add().int64_list.value.extend(lab_int_list)

        seqex_list.append(seqex)
        key = seqex.context.feature['patientId'].bytes_list.value[0]
        key_list.append(key)

    print('Filtered encounters due to duplicate codes: %d' % num_duplicate)
    print('Filtered encounters due to thresholding: %d' % num_cut)
    print('Average num_dx_ids: %f' % (num_dx_ids / count))
    print('Average num_treatments: %f' % (num_treatments / count))
    print('Average num_labs: %f' % (num_labs/ count))
    print('Average num_unique_dx_ids: %f' % (num_unique_dx_ids / count))
    print('Average num_unique_treatments: %f' % (num_unique_treatments / count))
    print('Average num_unique_labs: %f' % (num_unique_labs / count))
    print('Min dx cut: %d' % min_dx_cut)
    print('Min treatment cut: %d' % min_treatment_cut)
    print('Min lab cut: %d' % min_lab_cut)
    print('Max dx cut: %d' % max_dx_cut)
    print('Max treatment cut: %d' % max_treatment_cut)
    print('Max lab cut: %d' % max_lab_cut)
    print('Number of labeled: %d' % num_labeled)
    return key_list, seqex_list, dx_str2int, treat_str2int, lab_str2int


def train_val_test_split(patient_ids, random_seed=100):
    train_ids, val_ids, test_ids = [], [], []
    # with open("../../../HeteroGNN/rawdata/train_ids.txt", "r") as f:
    #     for line in f.readlines():
    #         train_ids.append(line.rstrip())
    # with open("../../../HeteroGNN/rawdata/val_ids.txt", "r") as f:
    #     for line in f.readlines():
    #         val_ids.append(line.rstrip())
    # with open("../../../HeteroGNN/rawdata/test_ids.txt", "r") as f:
    #     for line in f.readlines():
    #         test_ids.append(line.rstrip())
    with open("train_ids.txt", "r") as f:
        for line in f.readlines():
            train_ids.append(line.rstrip())
    with open("val_ids.txt", "r") as f:
        for line in f.readlines():
            val_ids.append(line.rstrip())
    with open("test_ids.txt", "r") as f:
        for line in f.readlines():
            test_ids.append(line.rstrip())
            
#     train_ids, test_ids = model_selection.train_test_split(patient_ids, test_size=0.2, random_state=random_seed)
#     test_ids, val_ids = model_selection.train_test_split(test_ids, test_size=0.5, random_state=random_seed)
    
    return train_ids, val_ids, test_ids


def get_partitions(seqex_list, id_set=None):
    total_visit = 0
    new_seqex_list = []
    for seqex in seqex_list:
        if total_visit % 1000 == 0:
            sys.stdout.write('Visit count: %d\r' % total_visit)
            sys.stdout.flush()
        key = seqex.context.feature['patientId'].bytes_list.value[0].decode('utf-8')
        if (id_set is not None and key not in id_set):
            total_visit += 1
            continue
        new_seqex_list.append(seqex)
    return new_seqex_list


def parser_fn(serialized_example):
    context_features_config = {
        'patientId': tf.io.VarLenFeature(tf.string),
        'label': tf.io.FixedLenFeature([1], tf.int64),
    }
    sequence_features_config = {
        'dx_ints': tf.io.VarLenFeature(tf.int64),
        'proc_ints': tf.io.VarLenFeature(tf.int64),
        'lab_ints': tf.io.VarLenFeature(tf.int64)
    }
    (batch_context, batch_sequence) = tf.io.parse_single_sequence_example(
        serialized_example,
        context_features=context_features_config,
        sequence_features=sequence_features_config)
    labels = tf.squeeze(tf.cast(batch_context['label'], tf.float32))
    return batch_sequence, labels


def tf2csr(output_path, partition, maps, labflag=False):
    num_epochs = 1
    buffer_size = 32
    dataset = tf.data.TFRecordDataset(output_path + partition + ".tfrecord")
    dataset = dataset.shuffle(buffer_size)
    dataset = dataset.repeat(num_epochs)
    dataset = dataset.map(parser_fn, num_parallel_calls=4)
    dataset = dataset.batch(1)
    dataset = dataset.prefetch(16)
    count = 0
    np_data = []
    np_label = []
    for data in dataset:
        count += 1
        # one data = multilabel of dx_ints + multilabel of proc_ints + multilabel of lab_ints
        np_datum = np.zeros(sum([len(m) for m in maps]))
        # <= this is the number of nodes? maybe ... (from line 68 in train.py)
        
        # one hot
        dx_pos = tf.sparse.to_dense(data[0]['dx_ints']).numpy().ravel()
        proc_pos = tf.sparse.to_dense(data[0]['proc_ints']).numpy().ravel() + \
                   sum([len(m) for m in maps[:1]])
        
        np_datum[dx_pos] = 1
        np_datum[proc_pos] = 1
        
        if labflag:
            lab_pos = tf.sparse.to_dense(data[0]['lab_ints']).numpy().ravel() + \
                  sum([len(m) for m in maps[:2]])
            np_datum[lab_pos] = 1
        
        np_data.append(np_datum)
        np_label.append(data[1].numpy()[0])
        sys.stdout.write('%d\r' % count)
        sys.stdout.flush()
        
    pickle.dump((csr_matrix(np.array(np_data)), np.array(np_label)), \
                open(output_path + partition + '_csr.pkl', 'wb'))


"""Set <input_path> to where the raw MIMIC CSV files are located.
Set <output_path> to where you want the output files to be.
"""
def main():
    parser = argparse.ArgumentParser(description='File path')
    parser.add_argument('--input_path', type=str, default='../../rawdata/mimic/', help='input path of original dataset')
    parser.add_argument('--label', type=int, default=0)
    parser.add_argument('--output_path', type=str, default='./', help='output path of processed dataset')
    parser.add_argument('--exist_lab', action="store_true")
    args = parser.parse_args()
    input_path = args.input_path
    output_path = args.output_path + str(args.label) + "/"
    if os.path.isdir(output_path) is False:
        os.makedirs(output_path)

    admission_dx_file = input_path + 'patient.csv' # '/ADMISSIONS.csv'
    diagnosis_file = input_path + 'medication.csv'  # '/DIAGNOSES_ICD.csv'
    treatment_file = input_path + 'procedure.csv' #'/PROCEDURES_ICD.csv'
#     encounter_dict = process_patient(admission_dx_file, {})
    patient2label = process_patient(admission_dx_file, {}, args.label)
    encounter_dict = process_diagnosis(diagnosis_file, patient2label)
    print(len(encounter_dict))
    encounter_dict = process_treatment(treatment_file, encounter_dict)
    print(len(encounter_dict))
    
    patient_ids = np.array([(encounter_dict[key].patient_id + ':' + encounter_dict[key].encounter_id) for key in encounter_dict])
#     patient_ids = np.array([encounter_dict[key].encounter_id for key in encounter_dict])

    print("# patients :", patient_ids.shape[0])
    train_ids, val_ids, test_ids = train_val_test_split(patient_ids)
    
    
    if args.exist_lab:
        lab_file = input_path + '/lab_final.csv'
        mean_std = get_lab_mean_std(lab_file, train_ids)
        encounter_dict = process_lab(lab_file, encounter_dict, mean_std)
    
    key_list, seqex_list, dx_map, proc_map, lab_map = build_seqex(encounter_dict, skip_duplicate=False, min_num_codes=1, max_num_codes=200, labflag=args.exist_lab)
#     print(key_list)
    train_seqex = get_partitions(seqex_list, set(train_ids))
    val_seqex = get_partitions(seqex_list, set(val_ids))
    test_seqex = get_partitions(seqex_list, set(test_ids))
    print("seqex")
    print(len(train_seqex), len(val_seqex), len(test_seqex), len(train_seqex)+len(val_seqex)+len(test_seqex))
    
    pickle.dump(dx_map, open(output_path + '/dx_map.p', 'wb'), -1)
    print("# dx:", len(dx_map))
    pickle.dump(proc_map, open(output_path + '/proc_map.p', 'wb'), -1)
    print("# proc:", len(proc_map))
    if args.exist_lab:
        pickle.dump(lab_map, open(output_path + '/lab_map.p', 'wb'), -1)
    print("Split done.")
    
#     print(val_seqex)
#     print(test_seqex)
    with tf.io.TFRecordWriter(output_path + '/train.tfrecord') as writer:
        for seqex in train_seqex:
            writer.write(seqex.SerializeToString())
    with tf.io.TFRecordWriter(output_path + '/validation.tfrecord') as writer:
        for seqex in val_seqex:
            writer.write(seqex.SerializeToString())
    with tf.io.TFRecordWriter(output_path + '/test.tfrecord') as writer:
        for seqex in test_seqex:
            writer.write(seqex.SerializeToString())
            
    for partition in ['train', 'validation', 'test']:
        tf2csr(output_path, partition, [dx_map, proc_map, lab_map], labflag=args.exist_lab)
    print('done')


if __name__ == '__main__':
    main()
