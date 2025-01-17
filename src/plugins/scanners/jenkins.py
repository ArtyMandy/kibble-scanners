#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
 #the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import time
import datetime
import re
import json
import hashlib
import plugins.utils.jsonapi
import threading
import requests.exceptions
import os

"""
This is the Kibble Jenkins scanner plugin.
"""

title = "Scanner for Jenkins CI"
version = "0.1.0"

def accepts(source):
    """ Determines whether we want to handle this source """
    if source['type'] == 'jenkins':
        return True
    return False


def scanJob(KibbleBit, source, job, creds):
    """ Scans a single job for activity """
    NOW = int(datetime.datetime.utcnow().timestamp())
    dhash = hashlib.sha224( ("%s-%s-%s" % (source['organisation'], source['sourceURL'], job['name']) ).encode('ascii', errors='replace')).hexdigest()
    found = True
    doc= None
    parseIt = False
    found = KibbleBit.exists('cijob', dhash)
    
    # Get $jenkins/job/$job-name/json...
    jobURL = os.path.join(source['sourceURL'], "job/%s/api/json?depth=2&tree=builds[number,status,timestamp,id,result,duration]" % job['name'])
    KibbleBit.pprint(jobURL)
    jobjson = plugins.utils.jsonapi.get(jobURL, auth = creds)
    
    # If valid JSON, ...
    if jobjson:
        for build in jobjson.get('builds', []):
            buildhash = hashlib.sha224( ("%s-%s-%s-%s" % (source['organisation'], source['sourceURL'], job['name'], build['id']) ).encode('ascii', errors='replace')).hexdigest()
            builddoc = None
            try:
                builddoc = KibbleBit.get('ci_build', buildhash)
            except:
                pass
            
            # If this build already completed, no need to parse it again
            if builddoc and builddoc.get('completed', False):
                continue
            
            KibbleBit.pprint("[%s-%s] This is new or pending, analyzing..." % (job['name'], build['id']))
            
            completed = True if build['result'] else False
            
            # Estimate time spent in queue
            queuetime = 0
            TS = int(build['timestamp']/1000)
            if builddoc:
                queuetime = builddoc.get('queuetime', 0)
            if not completed:
                queuetime = NOW - TS
            
            # Get build status (success, failed, canceled etc)
            status = 'building'
            if build['result'] in ['SUCCESS', 'STABLE']:
                status = 'success'
            if build['result'] in ['FAILURE', 'UNSTABLE']:
                status = 'failed'
            if build['result'] in ['ABORTED']:
                status = 'aborted'
            
            # Calc when the build finished (jenkins doesn't show this)
            if completed:
                FIN = int(build['timestamp']/1000) + build['duration']
            else:
                FIN = 0
                
            doc = {
                # Build specific data
                'id': buildhash,
                'date': time.strftime("%Y/%m/%d %H:%M:%S", time.gmtime(FIN)),
                'buildID': build['id'],
                'completed': completed,
                'duration': build['duration'],
                'job': job['name'],
                'jobURL': jobURL,
                'status': status,
                'started': int(build['timestamp']/1000),
                'ci': 'jenkins',
                'queuetime': queuetime,
                
                # Standard docs values
                'sourceID': source['sourceID'],
                'organisation': source['organisation'],
                'upsert': True,
            }
            KibbleBit.append('ci_build', doc)
        # Yay, it worked!
        return True
    
    # Boo, it failed!
    KibbleBit.pprint("Fetching job data failed!")
    return False


class jenkinsThread(threading.Thread):
    """ Generic thread class for scheduling multiple scans at once """
    def __init__(self, block, KibbleBit, source, creds, jobs):
        super(jenkinsThread, self).__init__()
        self.block = block
        self.KibbleBit = KibbleBit
        self.creds = creds
        self.source = source
        self.jobs = jobs
        
    def run(self):
        badOnes = 0
        while len(self.jobs) > 0 and badOnes <= 50:
            self.block.acquire()
            try:
                job = self.jobs.pop(0)
            except Exception as err:
                self.block.release()
                return
            if not job:
                self.block.release()
                return
            self.block.release()
            if not scanJob(self.KibbleBit, self.source, job, self.creds):
                self.KibbleBit.pprint("[%s] This borked, trying another one" % job['name'])
                badOnes += 1
                if badOnes > 100:
                    self.KibbleBit.pprint("Too many errors, bailing!")
                    self.source['steps']['issues'] = {
                        'time': time.time(),
                        'status': 'Too many errors while parsing at ' + time.strftime("%Y/%m/%d %H:%M:%S", time.gmtime(time.time())),
                        'running': False,
                        'good': False
                    }
                    self.KibbleBit.updateSource(self.source)
                    return
            else:
                badOnes = 0

def scan(KibbleBit, source):
    # Simple URL check
    jenkins = re.match(r"(https?://.+)", source['sourceURL'])
    if jenkins:
        
        source['steps']['jenkins'] = {
            'time': time.time(),
            'status': 'Parsing Jenkins job changes...',
            'running': True,
            'good': True
        }
        KibbleBit.updateSource(source)
        
        badOnes = 0
        pendingJobs = []
        KibbleBit.pprint("Parsing Jenkins activity at %s" % source['sourceURL'])
        source['steps']['issues'] = {
            'time': time.time(),
            'status': 'Downloading changeset',
            'running': True,
            'good': True
        }
        KibbleBit.updateSource(source)
        
        # Jenkins may neeed credentials
        creds = None
        if source['creds'] and 'username' in source['creds'] and source['creds']['username'] and len(source['creds']['username']) > 0:
            creds = "%s:%s" % (source['creds']['username'], source['creds']['password'])
            
        # Get the job list
        sURL = source['sourceURL']
        KibbleBit.pprint("Getting job list...")
        jobsjs = plugins.utils.jsonapi.get("%s/api/json?tree=jobs[name,color]&depth=1" % sURL , auth = creds)
        
        # Get the current queue
        KibbleBit.pprint("Getting job queue...")
        queuejs = plugins.utils.jsonapi.get("%s/queue/api/json?depth=1" % sURL , auth = creds)
        
        # Save queue snapshot
        NOW = int(datetime.datetime.utcnow().timestamp())
        queuehash = hashlib.sha224( ("%s-%s-queue-%s" % (source['organisation'], source['sourceURL'], int(time.time())) ).encode('ascii', errors='replace')).hexdigest()
        
        
        # Scan queue items
        blocked = 0
        stuck = 0
        totalqueuetime = 0
        items = queuejs.get('items', [])
        
        for item in items:
            if item['blocked']:
                blocked += 1
            if item['stuck']:
                stuck += 1
            if 'inQueueSince' in item:
                totalqueuetime += (NOW - int(item['inQueueSince']/1000))
        
        avgqueuetime = totalqueuetime / max(1, len(items))
        
        # Count how many jobs are building
        building = 0
        for job in jobsjs.get('jobs', []):
            if 'anime' in job.get('color', ''): # a running job will have foo_anime as color
                building += 1
        
        # Write up a queue doc
        queuedoc = {
            'id': queuehash,
            'date': time.strftime("%Y/%m/%d %H:%M:%S", time.gmtime(NOW)),
            'time': NOW,
            'building': building,
            'size': len(items),
            'blocked': blocked,
            'stuck': stuck,
            'avgwait': avgqueuetime,
            'ci': 'jenkins',
            
            # Standard docs values
            'sourceID': source['sourceID'],
            'organisation': source['organisation'],
            'upsert': True,
        }
        KibbleBit.append('ci_queue', queuedoc)
        
        
        pendingJobs = jobsjs.get('jobs', [])
        KibbleBit.pprint("Found %u jobs in Jenkins" % len(pendingJobs))
        
        threads = []
        block = threading.Lock()
        KibbleBit.pprint("Scanning jobs using 4 sub-threads")
        for i in range(0,4):
            t = jenkinsThread(block, KibbleBit, source, creds, pendingJobs)
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()

        # We're all done, yaay        
        KibbleBit.pprint("Done scanning %s" % source['sourceURL'])

        source['steps']['issues'] = {
            'time': time.time(),
            'status': 'Jenkins successfully scanned at ' + time.strftime("%Y/%m/%d %H:%M:%S", time.gmtime(time.time())),
            'running': False,
            'good': True
        }
        KibbleBit.updateSource(source)
    