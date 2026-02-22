#!/usr/bin/env python3
import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from stacks.trade_store_stack import TradeStoreConfig, TradeStoreStack

app = cdk.App()

TradeStoreStack(
    app,
    "TradeStore-Staging",
    config=TradeStoreConfig(
        environment="staging",
        vpc_cidr="10.1.0.0/16",
        rds_instance_type=ec2.InstanceType.of(
            ec2.InstanceClass.T3, ec2.InstanceSize.MICRO
        ),
        multi_az=False,
        kafka_instance_type="kafka.t3.small",
        kafka_broker_count=2,
        api_cpu=256,
        api_memory=512,
        consumer_cpu=256,
        consumer_memory=512,
        api_desired_count=1,
        consumer_desired_count=1,
        enable_autoscaling=False,
    ),
    env=cdk.Environment(region="ap-south-1"),
)

TradeStoreStack(
    app,
    "TradeStore-Prod",
    config=TradeStoreConfig(
        environment="prod",
        vpc_cidr="10.0.0.0/16",
        rds_instance_type=ec2.InstanceType.of(
            ec2.InstanceClass.T3, ec2.InstanceSize.MEDIUM
        ),
        multi_az=True,
        kafka_instance_type="kafka.m5.large",
        kafka_broker_count=3,
        api_cpu=1024,
        api_memory=2048,
        consumer_cpu=512,
        consumer_memory=1024,
        api_desired_count=2,
        consumer_desired_count=2,
        enable_autoscaling=True,
        autoscaling_min=2,
        autoscaling_max=6,
    ),
    env=cdk.Environment(region="ap-south-1"),
)

app.synth()
