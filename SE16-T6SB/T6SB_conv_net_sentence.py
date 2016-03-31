# -*- coding: utf-8 -*-
"""
Modified from sample code:
Convolutional Neural Networks for Sentence Classification
http://arxiv.org/pdf/1408.5882v2.pdf

Much of the code is modified from
- deeplearning.net (for ConvNet classes)
- https://github.com/mdenil/dropout (for dropout)
- https://groups.google.com/forum/#!topic/pylearn-dev/3QbKtCumAW4 (for Adadelta)
"""
import cPickle
import numpy as np
from collections import defaultdict, OrderedDict
import theano
import theano.tensor as T
import re
import warnings
import sys
warnings.filterwarnings("ignore")   

#different non-linearities
def ReLU(x):
    y = T.maximum(0.0, x)
    return(y)
def Sigmoid(x):
    y = T.nnet.sigmoid(x)
    return(y)
def Tanh(x):
    y = T.tanh(x)
    return(y)
def Iden(x):
    y = x
    return(y)

	
def vote_for_answer(test_vote_array,test_set_y,perf_or_predict):
    test_real_size = test_vote_array.shape[0]
    group = test_vote_array.shape[1]
    test_result = np.zeros((test_real_size,1))
    for i in range(test_real_size):
        max_vote = max(test_vote_array[i])
        for j in range(group):
            if test_vote_array[i][j] == max_vote:
                test_result[i] = j
                break
    sum_4_test = 0
    for i in range(test_real_size):
        if test_result[i] == test_set_y[i]:
            sum_4_test += 1
    final_test_perf = float(sum_4_test) / test_real_size
    if perf_or_predict == 0:
        return final_test_perf
    if perf_or_predict == 1:
        print "perf: " + str(final_test_perf) + "\n"
        return test_result
	
def train_conv_net(use_test,
                   perf_or_predict,
                   datasets,
                   U,
                   img_w=300, 
                   filter_hs=[3,4,5],
                   hidden_units=[100,2], 
                   dropout_rate=[0.5],
                   shuffle_batch=True,
                   n_epochs=25, 
                   batch_size=50, 
                   lr_decay = 0.95,
                   conv_non_linear="relu",
                   activations=[Iden],
                   sqr_norm_lim=9,
                   non_static=True):
    """
    Train a simple conv net
    img_h = sentence length (padded where necessary)
    img_w = word vector length (300 for word2vec)
    filter_hs = filter window sizes    
    hidden_units = [x,y] x is the number of feature maps (per filter window), and y is the penultimate layer
    sqr_norm_lim = s^2 in the paper
    lr_decay = adadelta decay parameter
    """    
    rng = np.random.RandomState(3435)
    img_h = len(datasets[0][0])-1  
    filter_w = img_w    
    feature_maps = hidden_units[0]
    filter_shapes = []
    pool_sizes = []
    test_real_size = datasets[1].shape[0]
    test_vote_array = np.zeros((datasets[1].shape[0],10))
    for filter_h in filter_hs:
        
        filter_shapes.append((feature_maps, 1, filter_h, filter_w))

        pool_sizes.append((img_h-filter_h+1, img_w-filter_w+1))
    parameters = [("image shape",img_h,img_w),("filter shape",filter_shapes), ("hidden_units",hidden_units),
                  ("dropout", dropout_rate), ("batch_size",batch_size),("non_static", non_static),
                    ("learn_decay",lr_decay), ("conv_non_linear", conv_non_linear), ("non_static", non_static)
                    ,("sqr_norm_lim",sqr_norm_lim),("shuffle_batch",shuffle_batch)]
    print parameters    
    
    #define model architecture
    index = T.lscalar()
    x = T.matrix('x')   
    y = T.ivector('y')
    Words = theano.shared(value = U, name = "Words")
    zero_vec_tensor = T.vector()
    zero_vec = np.zeros(img_w)

    set_zero = theano.function([zero_vec_tensor], updates=[(Words, T.set_subtensor(Words[0,:], zero_vec_tensor))])

    layer0_input = Words[T.cast(x.flatten(),dtype="int32")].reshape((x.shape[0],1,x.shape[1],Words.shape[1]))                                  
    conv_layers = []
    layer1_inputs = []

    for i in xrange(len(filter_hs)):
        filter_shape = filter_shapes[i]
        pool_size = pool_sizes[i]
        conv_layer = LeNetConvPoolLayer(rng, input=layer0_input,image_shape=(batch_size, 1, img_h, img_w),
                                filter_shape=filter_shape, poolsize=pool_size, non_linear=conv_non_linear)
        layer1_input = conv_layer.output.flatten(2)
        conv_layers.append(conv_layer)
        layer1_inputs.append(layer1_input)
    layer1_input = T.concatenate(layer1_inputs,1)
    hidden_units[0] = feature_maps*len(filter_hs)    
    classifier = MLPDropout(rng, input=layer1_input, layer_sizes=hidden_units, activations=activations, dropout_rates=dropout_rate)
    
    #define parameters of the model and update functions using adadelta
    params = classifier.params     
    for conv_layer in conv_layers:
        params += conv_layer.params
    if non_static:
        #if word vectors are allowed to change, add them as model parameters
        params += [Words]

    cost = classifier.negative_log_likelihood(y) 
    dropout_cost = classifier.dropout_negative_log_likelihood(y)           
    grad_updates = sgd_updates_adadelta(params, dropout_cost, lr_decay, 1e-6, sqr_norm_lim)
    
    #shuffle dataset and assign to mini batches. if dataset size is not a multiple of mini batches, replicate 
    #extra data (at random)
    np.random.seed()
    if datasets[0].shape[0] % batch_size > 0:
        extra_data_num = batch_size - datasets[0].shape[0] % batch_size
        train_set = np.random.permutation(datasets[0])   
        extra_data = train_set[:extra_data_num]
        new_data=np.append(datasets[0],extra_data,axis=0)
    else:
        new_data = datasets[0]
    if use_test == 1 and datasets[1].shape[0] % batch_size > 0:
        extra_data_num = batch_size - datasets[1].shape[0] % batch_size
        extra_data = datasets[1][:extra_data_num]
        datasets[1] = np.append(datasets[1],extra_data,axis=0)
        
    new_data = np.random.permutation(new_data)
    n_batches = new_data.shape[0]/batch_size
    n_train_batches = int(np.round(n_batches*0.9))
    if use_test == 1:
        n_test_batches = int(np.round(datasets[1].shape[0]/batch_size))
    #divide train set into train/val sets 
    test_set_x_4check = datasets[1][:,:img_h] 
    test_set_y_4check = np.asarray(datasets[1][:,-1],"int32")
    train_set = new_data[:n_train_batches*batch_size,:]
    val_set = new_data[n_train_batches*batch_size:,:]     
    train_set_x, train_set_y = shared_dataset((train_set[:,:img_h],train_set[:,-1]))
    val_set_x, val_set_y = shared_dataset((val_set[:,:img_h],val_set[:,-1]))
    test_set_x, test_set_y = shared_dataset((datasets[1][:,:img_h],datasets[1][:,-1]))
    n_val_batches = n_batches - n_train_batches
    val_model = theano.function([index], classifier.errors(y),
         givens={
            x: val_set_x[index * batch_size: (index + 1) * batch_size],
            y: val_set_y[index * batch_size: (index + 1) * batch_size]})	
    #compile theano functions to get train/val/test errors
    test_model = theano.function([index], classifier.errors(y),
         givens={
            x: train_set_x[index * batch_size: (index + 1) * batch_size],
            y: train_set_y[index * batch_size: (index + 1) * batch_size]})               
    train_model = theano.function([index], cost, updates=grad_updates,
          givens={
            x: train_set_x[index*batch_size:(index+1)*batch_size],
            y: train_set_y[index*batch_size:(index+1)*batch_size]})
    			
    get_test_label = theano.function([index], classifier.testlabel(),
         givens={
            x: test_set_x[index * batch_size: (index + 1) * batch_size]})    
            
    get_possible_label = theano.function([index], classifier.pred_possible_label(),
         givens={
            x: test_set_x[index * batch_size: (index + 1) * batch_size]})

    test_pred_layers = []
    if use_test == 1:
        test_size = batch_size
    else:
        test_size = datasets[1].shape[0]
    test_layer0_input = Words[T.cast(x.flatten(),dtype="int32")].reshape((test_size,1,img_h,Words.shape[1]))
    for conv_layer in conv_layers:
        test_layer0_output = conv_layer.predict(test_layer0_input, test_size)
        test_pred_layers.append(test_layer0_output.flatten(2))
    test_layer1_input = T.concatenate(test_pred_layers, 1)
    test_y_pred = classifier.predict(test_layer1_input)
    test_y_pred2 = classifier.predict_possible_label(test_layer1_input)
    #test_error = T.mean(T.neq(test_y_pred, y))
    #test_model_all = theano.function([x,y], test_error)  	
    get_test_result = theano.function([x],test_y_pred)
    get_possible_result = theano.function([x],test_y_pred2)

    #start training over mini-batches
    print '... training'
    epoch = 0
    best_test_perf = 0
    final_test_perf = 0
    predict_vector = []
    val_perf = 0
    test_perf = 0       
    cost_epoch = 0    
    while (epoch < n_epochs):        
        epoch = epoch + 1
        if shuffle_batch:
            for minibatch_index in np.random.permutation(range(n_train_batches)):
                cost_epoch = train_model(minibatch_index)
                set_zero(zero_vec)
        else:
            for minibatch_index in xrange(n_train_batches):
                cost_epoch = train_model(minibatch_index)  
                set_zero(zero_vec)
        train_losses = [test_model(i) for i in xrange(n_train_batches)]
        train_perf = 1 - np.mean(train_losses)
        val_losses = [val_model(i) for i in xrange(n_val_batches)]
        val_perf = 1- np.mean(val_losses)
        print('epoch %i, train perf %f %%, val perf %f' % (epoch, train_perf * 100., val_perf*100.))
        if epoch>=6 and epoch%2 == 0:
            #best_val_perf = val_perf
            if use_test == 1:
                test_result = []
                for minibatch_index in xrange(n_test_batches):
                    #test_result_tmp = get_test_label(minibatch_index)
                    #test_result_tmp = np.array(test_result_tmp)
                    possible_label = get_possible_label(minibatch_index)
                    max_label = get_test_label(minibatch_index)
                    """ perform 3-class classification on 2-class label data"""
                    for pi in range(len(possible_label)):
                        if possible_label[pi] <= 0.1:
                            max_label[pi] = 2
                    test_result_tmp = np.array(max_label)
                    test_result.append(test_result_tmp)
                test_result = np.array(test_result)
                test_result = test_result.reshape((n_test_batches * batch_size,1))
                for i in range(test_real_size):
                    test_vote_array[i][test_result[i]]+=1
                sum_4_test = 0
                for i in range(test_real_size):
                    if test_result[i] == test_set_y_4check[i]:
                        sum_4_test += 1	  				
                test_perf = float(sum_4_test)/test_real_size 
                if test_perf > best_test_perf:
                    best_test_perf = test_perf			
                print("test_perf: " + str(test_perf))		
            if use_test == 0:
                #test_result = get_test_result(test_set_x_4check)
                #test_result = np.array(test_result)  
                possible_result = get_possible_result(test_set_x_4check)
                max_result = get_test_result(test_set_x_4check)
                """ perform 3-class classification on 2-class label data"""
                for pi in range(len(possible_result)):
                    if possible_result[pi]<=0.1:
                        max_result[pi] = 2
                test_result = np.array(max_result)
                for i in range(test_real_size):
                    test_vote_array[i][test_result[i]]+=1
                sum_4_test = 0
                for i in range(test_real_size):
                    if test_result[i] == test_set_y_4check[i]:
                        sum_4_test += 1				
                test_perf = float(sum_4_test)/test_real_size          
                if test_perf > best_test_perf:
                    best_test_perf = test_perf						
                print("test_perf: " + str(test_perf))

    if epoch == n_epochs:
        if perf_or_predict == 0:
            final_test_perf = vote_for_answer(test_vote_array,test_set_y_4check,perf_or_predict)
            return final_test_perf 
        if perf_or_predict == 1:
            predict_vector = vote_for_answer(test_vote_array,test_set_y_4check,perf_or_predict)
            return predict_vector			

def shared_dataset(data_xy, borrow=True):
        """ Function that loads the dataset into shared variables

        The reason we store our dataset in shared variables is to allow
        Theano to copy it into the GPU memory (when code is run on GPU).
        Since copying data into the GPU is slow, copying a minibatch everytime
        is needed (the default behaviour if the data is not in a shared
        variable) would lead to a large decrease in performance.
        """
        data_x, data_y = data_xy
        shared_x = theano.shared(np.asarray(data_x,
                                               dtype=theano.config.floatX),
                                 borrow=borrow)
        shared_y = theano.shared(np.asarray(data_y,
                                               dtype=theano.config.floatX),
                                 borrow=borrow)
        return shared_x, T.cast(shared_y, 'int32')
        
def sgd_updates_adadelta(params,cost,rho=0.95,epsilon=1e-6,norm_lim=9,word_vec_name='Words'):
    """
    adadelta update rule, mostly from
    https://groups.google.com/forum/#!topic/pylearn-dev/3QbKtCumAW4 (for Adadelta)
    """
    updates = OrderedDict({})
    exp_sqr_grads = OrderedDict({})
    exp_sqr_ups = OrderedDict({})
    gparams = []
    for param in params:
        empty = np.zeros_like(param.get_value())
        exp_sqr_grads[param] = theano.shared(value=as_floatX(empty),name="exp_grad_%s" % param.name)
        gp = T.grad(cost, param)
        exp_sqr_ups[param] = theano.shared(value=as_floatX(empty), name="exp_grad_%s" % param.name)
        gparams.append(gp)
    for param, gp in zip(params, gparams):
        exp_sg = exp_sqr_grads[param]
        exp_su = exp_sqr_ups[param]
        up_exp_sg = rho * exp_sg + (1 - rho) * T.sqr(gp)
        updates[exp_sg] = up_exp_sg
        step =  -(T.sqrt(exp_su + epsilon) / T.sqrt(up_exp_sg + epsilon)) * gp
        updates[exp_su] = rho * exp_su + (1 - rho) * T.sqr(step)
        stepped_param = param + step
        if (param.get_value(borrow=True).ndim == 2) and (param.name!='Words'):
            col_norms = T.sqrt(T.sum(T.sqr(stepped_param), axis=0))
            desired_norms = T.clip(col_norms, 0, T.sqrt(norm_lim))
            scale = desired_norms / (1e-7 + col_norms)
            updates[param] = stepped_param * scale
        else:
            updates[param] = stepped_param      
    return updates 

def as_floatX(variable):
    if isinstance(variable, float):
        return np.cast[theano.config.floatX](variable)

    if isinstance(variable, np.ndarray):
        return np.cast[theano.config.floatX](variable)
    return theano.tensor.cast(variable, theano.config.floatX)
    
def safe_update(dict_to, dict_from):
    """
    re-make update dictionary for safe updating
    """
    for key, val in dict(dict_from).iteritems():
        if key in dict_to:
            raise KeyError(key)
        dict_to[key] = val
    return dict_to
    
def get_idx_from_sent(sent, word_idx_map, max_l=51, k=300, filter_h=5):
    """
    Transforms sentence into a list of indices. Pad with zeroes.
    """
    x = []
    pad = filter_h - 1
    for i in xrange(pad):
        x.append(0)
    words = sent.split()
    for word in words:
        if word in word_idx_map:
            x.append(word_idx_map[word])
    while len(x) < max_l+2*pad:
        x.append(0)
    return x

def make_idx_data_cv(revs, word_idx_map, cv, max_l=51, k=300, filter_h=5):
    """
    Transforms sentences into a 2-d matrix.
    """
    train, test = [], []
    for rev in revs:
        sent = get_idx_from_sent(rev["text"], word_idx_map, max_l, k, filter_h)   
        sent.append(rev["y"])
        if rev["split"]==cv:            
            test.append(sent)        
        else:  
            train.append(sent)   
    train = np.array(train,dtype="int")
    test = np.array(test,dtype="int")
    return [train, test]     
  
def make_idx_data(revs_train, revs_test, word_idx_map, max_l=51, k=300, filter_h=5):
    """
    Transforms sentences into a 2-d matrix.
    """
    train, test= [] ,[]
    for rev in revs_train:
        sent = get_idx_from_sent(rev["text"], word_idx_map, max_l, k, filter_h)   
        sent.append(rev["y"])
        train.append(sent)
    for rev in revs_test:
        sent = 	get_idx_from_sent(rev["text"], word_idx_map, max_l, k, filter_h)
        sent.append(rev["y"])
        test.append(sent)
    train = np.array(train,dtype="int")
    test = np.array(test,dtype="int")
    return [train, test]     

  
if __name__=="__main__":
    print "loading data...",
    x = cPickle.load(open("mr.p","rb"))
    revs, revs_test, W, W2, word_idx_map, vocab = x[0], x[1], x[2], x[3], x[4], x[5]
    print "data loaded!"
    mode= sys.argv[1]
    word_vectors = sys.argv[2] 
    print_answer_mode = sys.argv[3]	
    if mode=="-nonstatic":
        print "model architecture: CNN-non-static"
        non_static=True
    elif mode=="-static":
        print "model architecture: CNN-static"
        non_static=False
    execfile("T6SB_conv_net_classes.py")    
    if word_vectors=="-rand":
        print "using: random vectors"
        U = W2
    elif word_vectors=="-word2vec":
        print "using: word2vec vectors"
        U = W
    if print_answer_mode == "-perf":
        print "print answer mode: perf"
        perf_or_predict = 0
    elif print_answer_mode == "-predict":
        print "print answer mode: predict"
        perf_or_predict = 1
    results = []
    r = range(0,10)
    
    if len(revs_test)==0:
        use_test = 0
    else:
        use_test = 1	
    for i in r:
     
        if use_test==0:
            datasets = make_idx_data_cv(revs, word_idx_map, i, max_l=56,k=300, filter_h=5)
        else:
            datasets = make_idx_data(revs, revs_test, word_idx_map, max_l=56,k=300, filter_h=5)
        ret = train_conv_net(use_test,
                              perf_or_predict,
		                      datasets,
                              U,
                              lr_decay=0.95,
                              filter_hs=[3,4,5],
                              conv_non_linear="relu",
                              hidden_units=[100,2], 
                              shuffle_batch=True, 
                              n_epochs=25, 
                              sqr_norm_lim=9,
                              non_static=non_static,
                              batch_size=50,
                              dropout_rate=[0.5])
        if perf_or_predict == 0:
            print "cv: " + str(i) + ", perf: " + str(ret)
        results.append(ret)
    results = np.array(results)
    if perf_or_predict == 1:
        predict_answer = np.zeros((len(results[0]),1))
        sum_for_predict = np.zeros((len(results[0]),10))
        for i in r:
            for j in range(len(results[0])):
                    sum_for_predict[j][int(results[i][j])]+=1
        for i in range(sum_for_predict.shape[0]):
            max_vote = max(sum_for_predict[i])
            for j in range(sum_for_predict.shape[1]):
                if sum_for_predict[i][j] == max_vote:
                    predict_answer[i] = j
					
        f = open("predict_T6SB.txt","wb")
        for i in range(predict_answer.shape[0]):
            print predict_answer[i][0]
            if predict_answer[i][0] == 0:
                f.write("AGAINST\n")
            if predict_answer[i][0] == 1:
                f.write("FAVOR\n")
            if predict_answer[i][0] == 2:
                f.write("NONE\n")
        f.close()
    if perf_or_predict == 0:
        print str(np.mean(results))
