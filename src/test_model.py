

import sys
import numpy as np
import cPickle as pickle
from collections import OrderedDict
import argparse

import theano
import theano.tensor as T
from theano import config
from Queue import heapq


import operator
import time
import warnings

# recall = # intersection of ground truth and prediciton/# ground truth
def recall_calculation(y_true, y_pred, top_top_rank=[20, 30, 40]):
	recall = list()
	for i in range(len(y_pred)):
		thisOne = list()
		codes = y_true[i]
		tops = y_pred[i]
		for rk in top_top_rank:
			thisOne.append(len(set(codes).intersection(set(tops[:rk])))*1.0/len(set(codes)))
		recall.append( thisOne )
	return (np.array(recall)).mean(axis=0).tolist()

	mean_duration = options['mean_duration']
	if options['useLogTime']:
		trueVec = np.log(np.array(trueVec) + options['logEps'])
	else:
		trueVec = np.array(trueVec)
	predVec = np.array(predVec)

	numerator = ((trueVec - predVec) ** 2).sum()
	denominator = ((trueVec - mean_duration) ** 2).sum()

	return 1.0 - (numerator / denominator)

def numpy_floatX(data):
	return np.asarray(data, dtype=config.floatX)

def init_tparams(params):
	tparams = OrderedDict()
	for key, value in params.iteritems():
		tparams[key] = theano.shared(value, name=key)
	return tparams

def GRU_layer(tparams, emb, layerIndex, hiddenDimSize, mask=None):
	timesteps = emb.shape[0]
	if emb.ndim == 3: n_samples = emb.shape[1]
	else: n_samples = 1

	W_rx = T.dot(emb, tparams['W_r_'+layerIndex])
	W_zx = T.dot(emb, tparams['W_z_'+layerIndex])
	Wx = T.dot(emb, tparams['W_'+layerIndex])

	def stepFn(stepMask, wrx, wzx, wx, h):
		r = T.nnet.sigmoid(wrx + T.dot(h, tparams['U_r_'+layerIndex]) + tparams['b_r_'+layerIndex])
		z = T.nnet.sigmoid(wzx + T.dot(h, tparams['U_z_'+layerIndex]) + tparams['b_z_'+layerIndex])
		h_tilde = T.tanh(wx + T.dot(r*h, tparams['U_'+layerIndex]) + tparams['b_'+layerIndex])
		h_new = z * h + ((1. - z) * h_tilde)
		h_new = stepMask[:, None] * h_new + (1. - stepMask)[:, None] * h
		return h_new#, output, time

	results, updates = theano.scan(fn=stepFn, sequences=[mask,W_rx,W_zx,Wx], outputs_info=T.alloc(numpy_floatX(0.0), n_samples, hiddenDimSize), name='GRU_layer'+layerIndex, n_steps=timesteps)

	return results

def setup_model(tparams, options,W_emb=None):
	x = T.tensor3('x', dtype=config.floatX)
	t = T.matrix('t', dtype=config.floatX)
	mask = T.matrix('mask', dtype=config.floatX)

	n_timesteps = x.shape[0]
	n_samples = x.shape[1]

	if 'W_emb' in tparams.keys():
		emb = T.dot(x, tparams['W_emb'])
	elif W_emb is not None:
		emb = T.dot(x, W_emb)
	else:
		emb = x

	if options['useTime']:
		emb = T.concatenate([t.reshape([n_timesteps,n_samples,1]), emb], axis=2)

	if options['useTopics']:
		topics = T.tensor3('topics', dtype=config.floatX)
		emb = T.concatenate([topics, emb], axis=2)
	else:
		topics = None

	inputVector = emb
	for i, hiddenDimSize in enumerate(options['hiddenDimSize']):
		memories = GRU_layer(tparams, inputVector, str(i), hiddenDimSize, mask=mask)
		inputVector = memories * 0.5

	def softmaxStep(memory2d):
		return T.nnet.softmax(T.dot(memory2d, tparams['W_output']) + tparams['b_output'])

	results, updates = theano.scan(fn=softmaxStep, sequences=[inputVector], outputs_info=None, name='softmax_layer', n_steps=n_timesteps)
	results = results * mask[:,:,None]

	duration = 0.0
	if options['useTime']:
		return x, t, topics, mask, results
	else:
		return x, topics, mask, results

def load_files(dataFile, labelFile, timeFile, topicFile=None):
	test_set_x = np.array(pickle.load(open(dataFile, 'rb')))
	test_set_y = np.array(pickle.load(open(labelFile, 'rb')))
	test_set_t = None
	test_set_topic = None

	if len(timeFile) > 0:
		test_set_t = np.array(pickle.load(open(timeFile, 'rb')))
	if len(topicFile) > 0:
		test_set_topic = np.array(pickle.load(open(topicFile, 'rb')))

	def len_argsort(seq):
		return sorted(range(len(seq)), key=lambda x: len(seq[x]))

	sorted_index = len_argsort(test_set_x)
	test_set_x = [test_set_x[i] for i in sorted_index]
	test_set_y = [test_set_y[i] for i in sorted_index]
	if len(timeFile) > 0:
		test_set_t = [test_set_t[i] for i in sorted_index]
	if len(topicFile) > 0:
		test_set_topic = [test_set_topic[i] for i in sorted_index]

	test_set = (test_set_x, test_set_y, test_set_t, test_set_topic)

	return test_set

def padMatrixWithTime(seqs, times, options,topics=None):
	lengths = np.array([len(seq) for seq in seqs]) - 1
	n_samples = len(seqs)
	maxlen = np.max(lengths)
	inputDimSize = options['inputDimSize']
	numClass = options['numClass']
	useTopics = options['useTopics']

	x = np.zeros((maxlen, n_samples, inputDimSize)).astype(config.floatX)
	t = np.zeros((maxlen, n_samples)).astype(config.floatX)
	mask = np.zeros((maxlen, n_samples)).astype(config.floatX)

	if useTopics:
		topicSize = options['topicSize']
		top = np.zeros((maxlen, n_samples, topicSize)).astype(config.floatX)
		for idx, topic in enumerate(topics):
			for tvec, tvalue in zip(top[:lengths[idx], idx,:], topic[:-1]):
				tvec = tvalue.copy()

	for idx, (seq,time) in enumerate(zip(seqs,times)):
		for xvec, subseq in zip(x[:,idx,:], seq[:-1]):
			xvec[subseq] = 1.
		mask[:lengths[idx], idx] = 1.
		t[:lengths[idx], idx] = time[:-1]

	if options['useLogTime']:
		t = np.log(t + options['logEps'])

	if useTopics:
		return x, t, top, mask, lengths
	else:
		return x, t, mask, lengths
def padMatrixWithoutTime(seqs, options,topics=None):
	lengths = np.array([len(seq) for seq in seqs]) - 1
	n_samples = len(seqs)
	maxlen = np.max(lengths)
	inputDimSize = options['inputDimSize']
	numClass = options['numClass']
	useTopics = options['useTopics']

	x = np.zeros((maxlen, n_samples, inputDimSize)).astype(config.floatX)
	mask = np.zeros((maxlen, n_samples)).astype(config.floatX)

	if useTopics:
		topicSize = options['topicSize']
		top = np.zeros((maxlen, n_samples, topicSize)).astype(config.floatX)
		for idx, topic in enumerate(topics):
			for tvec, tvalue in zip(top[:lengths[idx], idx,:], topic[:-1]):
				tvec = tvalue.copy()

	for idx, seq in enumerate(seqs):
		for xvec, subseq in zip(x[:,idx,:], seq[:-1]):
			xvec[subseq] = 1.
		mask[:lengths[idx], idx] = 1.

	if useTopics:
		return x, top, mask, lengths
	else:
		return x, mask, lengths

def test_model(
	modelFile='model.txt',
	seqFile='seq.txt',
	inputDimSize=20000,
	labelFile='label.txt',
	numClass=500,
	timeFile='',
	predictTime=False,
	useLogTime=True,
	hiddenDimSize=[200,200],
	batchSize=100,
	logEps=1e-8,
	mean_duration=20.0,
	verbose=False,
	embFile='embFile.txt',
	topicSize=20,
	topicFile='',
	experiment='1'
):
	options = locals().copy()

	if len(timeFile) > 0: useTime = True
	else: useTime = False
	options['useTime'] = useTime

	if len(topicFile) > 0: useTopics = True
	else: useTopics = False
	options['useTopics'] = useTopics

	models = np.load(modelFile)
	tparams = init_tparams(models)
	# print( tparams.keys()

	def load_embedding(infile):
		Wemb = np.array(pickle.load(open(infile, 'rb'))).astype(config.floatX)
		return Wemb

	if len(embFile)>0:
		W_emb = load_embedding(embFile)
	else:
		W_emb = None

	print( 'build model ... ')
	if useTime and useTopics:
		x, t, topics, mask, codePred = setup_model(tparams, options, W_emb)
		predict_code = theano.function(inputs=[x,t,topics,mask], outputs=codePred, name='predict_code')
	elif useTime:
		x, t, topics, mask, codePred = setup_model(tparams, options, W_emb)
		predict_code = theano.function(inputs=[x,t,mask], outputs=codePred, name='predict_code')
	elif useTopics:
		x, topics, mask, codePred = setup_model(tparams, options, W_emb)
		predict_code = theano.function(inputs=[x,topics,mask], outputs=codePred, name='predict_code')
	else:
		x, topics, mask, codePred = setup_model(tparams, options, W_emb)
		predict_code = theano.function(inputs=[x,mask], outputs=codePred, name='predict_code')

	if 'W_emb' in models.keys():
		options['inputDimSize']=models['W_emb'].shape[0]
	elif W_emb is not None:
		options['inputDimSize']=W_emb.shape[0]

	options['numClass']=models['b_output'].shape[0]
	print( 'load data ... ')
	testSet = load_files(seqFile, labelFile, timeFile, topicFile)
	n_batches = int(np.ceil(float(len(testSet[0])) / float(batchSize)))
	print( 'done')

	predVec = []
	trueVec = []
	predTimeVec = []
	trueTimeVec = []
	iteration = 0
	for batchIndex in range(n_batches):
		tempX = testSet[0][batchIndex*batchSize: (batchIndex+1)*batchSize]
		tempY = testSet[1][batchIndex*batchSize: (batchIndex+1)*batchSize]
		if useTime and useTopics:
			tempT = testSet[2][batchIndex*batchSize: (batchIndex+1)*batchSize]
			tempTopics = testSet[3][batchIndex*batchSize: (batchIndex+1)*batchSize]
			x, t, topics, mask, lengths = padMatrixWithTime(tempX, tempT, options,tempTopics)
			codeResults = predict_code(x, t, topics, mask)
		elif useTime:
			tempT = testSet[2][batchIndex*batchSize: (batchIndex+1)*batchSize]
			x, t, mask, lengths = padMatrixWithTime(tempX, tempT, options)
			codeResults = predict_code(x, t, mask)
		elif useTopics:
			tempTopics = testSet[3][batchIndex*batchSize: (batchIndex+1)*batchSize]
			x, topics, mask, lengths = padMatrixWithoutTime(tempX, options,tempTopics) # todo
			codeResults = predict_code(x, topics, mask)
		else:
			x, mask, lengths = padMatrixWithoutTime(tempX, options)
			codeResults = predict_code(x, mask)

		for i in range(codeResults.shape[1]):
			tensorMatrix = codeResults[:,i,:]
			thisY = tempY[i][1:]
			for timeIndex in range(lengths[i]):
				if len(thisY[timeIndex]) == 0: continue
				trueVec.append(thisY[timeIndex])
				output = tensorMatrix[timeIndex]
				predVec.append(zip(*heapq.nlargest(30, enumerate(output), key=operator.itemgetter(1)))[0])

		if (iteration % 10 == 0) and verbose: print( 'iteration:%d/%d' % (iteration, n_batches))
		iteration += 1
		if iteration == 10: break

	# pickle.dump(trueVec,open('experiments/trueVec_exp' + str(experiment),'wr'))
	# pickle.dump(predVec,open('experiments/predVec_exp' + str(experiment),'wr'))
	recall = recall_calculation(trueVec, predVec)
	# for cross validation
	# pickle.dump(recall,open('experiments-cv/recall' + str(experiment),'wr'))

	# for experiments
	pickle.dump(recall,open('experiments/recall' + str(experiment),'w'))

	print( 'recall@10:%f, recall@20:%f, recall@30:%f' % (recall[0], recall[1], recall[2]))

def parse_arguments(parser):
	parser.add_argument('model_file', type=str, metavar='<model_file>', help='The path to the model file saved by Doctor AI')
	parser.add_argument('seq_file', type=str, metavar='<visit_file>', help='The path to the Pickled file containing visit information of patients')
	parser.add_argument('label_file', type=str, metavar='<label_file>', help='The path to the Pickled file containing label information of patients')
	parser.add_argument('hidden_dim_size', type=str, metavar='<hidden_dim_size>', help='The size of the hidden layers of the Doctor AI. This is a string argument. For example, [500,400] means you are using a two-layer GRU where the lower layer uses a 500-dimensional hidden layer, and the upper layer uses a 400-dimensional hidden layer. (default value: [200,200])')
	parser.add_argument('--time_file', type=str, default='', help='The path to the Pickled file containing durations between visits of patients. If you are not using duration information, do not use this option')
	parser.add_argument('--predict_time', type=int, default=0, choices=[0,1], help='Use this option if you want Doctor AI to also predict the time duration until the next visit (0 for false, 1 for true) (default value: 0)')
	parser.add_argument('--use_log_time', type=int, default=1, choices=[0,1], help='Use logarithm of time duration to dampen the impact of the outliers (0 for false, 1 for true) (default value: 1)')
	parser.add_argument('--batch_size', type=int, default=100, help='The size of a single mini-batch (default value: 100)')
	parser.add_argument('--mean_duration', type=float, default=20.0, help='The mean value of the durations between visits of the training data. This will be used to calculate the R^2 error (default value: 20.0)')
	parser.add_argument('--verbose', action='store_true', help='print( output after every 10 mini-batches (default false)')

	parser.add_argument('--input_dim_size', type=int, default=20000, help='The size of input dimension (default value: 20000)')
	parser.add_argument('--embed_file',  type=str, default='', help='The path to the Pickled file containing embedding information of input vector')
	parser.add_argument('--topic_size', type=int, default=20000, help='The size of input dimension (default value: 20000)')
	parser.add_argument('--topic_file',  type=str, default='', help='The path to the Pickled file containing topic information')
	parser.add_argument('--experiment', type=str, default='1', help='The order of experiment (default value: 1)')

	args = parser.parse_args()
	return args

if __name__ == '__main__':
	parser = argparse.ArgumentParser()
	args = parse_arguments(parser)
	hiddenDimSize = [int(strDim) for strDim in args.hidden_dim_size[1:-1].split(',')]

	test_model(
		modelFile=args.model_file,
		seqFile=args.seq_file,
		labelFile=args.label_file,
		timeFile=args.time_file,
		predictTime=args.predict_time,
		useLogTime=args.use_log_time,
		hiddenDimSize=hiddenDimSize,
		batchSize=args.batch_size,
		mean_duration=args.mean_duration,
		verbose=args.verbose,

		inputDimSize=args.input_dim_size,
		embFile=args.embed_file,
		topicSize=args.topic_size,
		topicFile=args.topic_file,
		experiment=args.experiment
	)
